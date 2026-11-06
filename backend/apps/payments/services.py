"""
Moving a payment through its statuses:

    PENDING -> CONFIRMED -> REVERSED
    PENDING -> REJECTED

Members are notified by apps.notifications.signals when the status changes.
"""

from django.db import transaction
from django.utils import timezone

from .models import Payment


class PaymentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _locked(payment):
    return Payment.objects.select_for_update().get(pk=payment.pk)


@transaction.atomic
def confirm(payment, by):
    payment = _locked(payment)

    if payment.status != Payment.Status.PENDING:
        raise PaymentError("E025", "Only pending payments can be confirmed.")

    payment.status = Payment.Status.CONFIRMED
    payment.confirmed_by = by
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])

    return payment


@transaction.atomic
def reject(payment, by, reason):
    payment = _locked(payment)

    if payment.status != Payment.Status.PENDING:
        raise PaymentError("E025", "Only pending payments can be rejected.")

    return _close(payment, Payment.Status.REJECTED, by, reason)


@transaction.atomic
def reverse(payment, by, reason):
    """
    Undo a confirmed payment, e.g. a bounced cheque or an M-Pesa reversal.
    It stops counting toward the member's deposit and balance; a plot
    already allocated stays allocated.
    """
    payment = _locked(payment)

    if payment.status != Payment.Status.CONFIRMED:
        raise PaymentError("E025", "Only confirmed payments can be reversed.")

    return _close(payment, Payment.Status.REVERSED, by, reason)


def _close(payment, new_status, by, reason):
    payment.status = new_status
    payment.status_changed_by = by
    payment.status_changed_at = timezone.now()
    payment.status_reason = reason.strip()
    payment.save(
        update_fields=[
            "status",
            "status_changed_by",
            "status_changed_at",
            "status_reason",
            "updated_at",
        ]
    )

    return payment
