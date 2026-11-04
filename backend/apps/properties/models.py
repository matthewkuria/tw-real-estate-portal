
from django.conf import settings
from django.db import models


class Property(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ALLOCATED = "ALLOCATED", "Allocated"

    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.PROTECT,
        related_name="properties",
    )
    plot_number = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    # Financial terms for this plot
    price = models.DecimalField(max_digits=12, decimal_places=2)
    required_deposit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    # A member can own multiple properties
    allocated_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="properties",
        null=True,
        blank=True,
        limit_choices_to={"role": "MEMBER"},
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
        db_index=True,
    )

    allocated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["estate", "plot_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["estate", "plot_number"],
                name="unique_plot_number_per_estate",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gt=0),
                name="property_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(required_deposit__gt=0),
                name="property_deposit_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    required_deposit__lte=models.F("price")
                ),
                name="property_deposit_not_above_price",
            ),
            # Status and holder always agree. Change them through
            # apps.properties.services, which also keeps the history.
            models.CheckConstraint(
                condition=(
                    models.Q(status="ALLOCATED", allocated_to__isnull=False)
                    | models.Q(
                        status="AVAILABLE",
                        allocated_to__isnull=True,
                        allocated_at__isnull=True,
                    )
                ),
                name="property_allocation_consistent",
            ),
        ]

    def __str__(self):
        return f"{self.estate} - Plot {self.plot_number}"


class PlotAllocation(models.Model):
    """
    One member's hold on a plot, from allocation until release. Rows are
    only ever closed, never rewritten, so they double as the audit trail.
    """

    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="plot_allocations",
    )

    allocated_at = models.DateTimeField()
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )

    # Confirmed payments toward the deposit when the plot was allocated.
    deposit_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Allocated before the full deposit was paid, on an admin's say-so.
    deposit_waived = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    release_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-allocated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property"],
                condition=models.Q(released_at__isnull=True),
                name="one_open_allocation_per_plot",
            ),
        ]

    def __str__(self):
        return f"{self.property} to {self.member}"
