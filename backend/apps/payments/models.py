
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Payment(models.Model):
    class Method(models.TextChoices):
        MPESA = "MPESA", "M-Pesa"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        CASH = "CASH", "Cash"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Confirmation"
        CONFIRMED = "CONFIRMED", "Confirmed"
        REJECTED = "REJECTED", "Rejected"
        REVERSED = "REVERSED", "Reversed"

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
        limit_choices_to={"role": "MEMBER"},
    )

    # The estate the member is paying toward.
    # Required even before a plot has been assigned.
    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.PROTECT,
        related_name="payments",
    )

    # Null until an administrator allocates a plot.
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="payments",
        null=True,
        blank=True,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    method = models.CharField(
        max_length=20,
        choices=Method.choices,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    received_at = models.DateTimeField(default=timezone.now)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments_recorded",
    )

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments_confirmed",
        null=True,
        blank=True,
    )

    confirmed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()

        if self.property_id and self.estate_id:
            if self.property.estate_id != self.estate_id:
                raise ValidationError({
                    "property": (
                        "The property must belong to the selected estate."
                    )
                })

        if self.status == self.Status.CONFIRMED:
            if not self.confirmed_by_id or not self.confirmed_at:
                raise ValidationError(
                    "A confirmed payment must record who confirmed it "
                    "and when."
                )

    def __str__(self):
        return (
            f"{self.member.email} - "
            f"KES {self.amount} - {self.status}"
        )
