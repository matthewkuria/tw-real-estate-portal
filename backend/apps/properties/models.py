
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
        ]

    def __str__(self):
        return f"{self.estate} - Plot {self.plot_number}"
