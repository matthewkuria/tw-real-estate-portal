"""
Allocating plots to members and releasing them. These are the only
places a plot's holder changes, so status, holder and history agree.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications import events
from apps.payments.models import Payment

from .models import PlotAllocation, Property


class AllocationError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


def deposit_paid(estate_id, member, plot_id=None):
    """
    Confirmed payments the member has made toward this plot: those not yet
    tied to a plot in its estate, plus any already tied to it.
    """
    on_plot = Q(property__isnull=True)

    if plot_id is not None:
        on_plot |= Q(property_id=plot_id)

    total = (
        Payment.objects
        .filter(on_plot, member=member, estate_id=estate_id, status=Payment.Status.CONFIRMED)
        .aggregate(total=Sum("amount"))["total"]
    )

    return total or Decimal("0")


def check_allocation(plot, member, waive_deposit=False, notes=""):
    """
    Why this member can't take this plot now, as an AllocationError.
    Returns the deposit paid so far when they can.
    """
    if plot.pk is not None and plot.status != Property.Status.AVAILABLE:
        raise AllocationError("E020", "This plot is already allocated. Release it first.")

    if member.role != User.Role.MEMBER:
        raise AllocationError("E021", "Plots can only be allocated to members.")

    if not member.is_active or member.status != User.Status.ACTIVE:
        raise AllocationError("E021", "This member's account isn't active.")

    paid = deposit_paid(plot.estate_id, member, plot.pk)

    if paid < plot.required_deposit:
        if not waive_deposit:
            raise AllocationError(
                "E022",
                "The member hasn't paid the required deposit yet.",
                {
                    "required_deposit": f"{plot.required_deposit:.2f}",
                    "deposit_paid": f"{paid:.2f}",
                    "shortfall": f"{plot.required_deposit - paid:.2f}",
                },
            )

        if not notes.strip():
            raise AllocationError("E022", "Give a reason for waiving the deposit.")

    return paid


@transaction.atomic
def allocate(plot, member, by, notes="", waive_deposit=False):
    plot = Property.objects.select_for_update().select_related("estate").get(pk=plot.pk)
    paid = check_allocation(plot, member, waive_deposit, notes)
    now = timezone.now()

    plot.allocated_to = member
    plot.status = Property.Status.ALLOCATED
    plot.allocated_at = now
    plot.save(update_fields=["allocated_to", "status", "allocated_at", "updated_at"])

    PlotAllocation.objects.create(
        property=plot,
        member=member,
        allocated_at=now,
        allocated_by=by,
        deposit_paid=paid,
        deposit_waived=paid < plot.required_deposit,
        notes=notes.strip(),
    )

    # Payments made toward the estate before a plot was chosen now belong
    # to this one.
    Payment.objects.filter(
        member=member,
        estate_id=plot.estate_id,
        property__isnull=True,
    ).update(property=plot, updated_at=now)

    events.plot_allocated(plot, by)

    return plot


@transaction.atomic
def release(plot, by, reason):
    """
    Free the plot. Its payments stay linked to it, under the old holder.
    """
    plot = Property.objects.select_for_update().select_related("estate").get(pk=plot.pk)

    if plot.status != Property.Status.ALLOCATED:
        raise AllocationError("E023", "This plot isn't allocated.")

    member = plot.allocated_to
    now = timezone.now()

    closed = PlotAllocation.objects.filter(property=plot, released_at__isnull=True).update(
        released_at=now,
        released_by=by,
        release_reason=reason,
    )

    if not closed:
        # Allocated outside this workflow; keep the history complete.
        PlotAllocation.objects.create(
            property=plot,
            member=member,
            allocated_at=plot.allocated_at or plot.updated_at,
            released_at=now,
            released_by=by,
            release_reason=reason,
        )

    plot.allocated_to = None
    plot.status = Property.Status.AVAILABLE
    plot.allocated_at = None
    plot.save(update_fields=["allocated_to", "status", "allocated_at", "updated_at"])

    events.plot_released(plot, member, by, reason)

    return plot
