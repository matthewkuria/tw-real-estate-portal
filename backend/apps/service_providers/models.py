from django.conf import settings
from django.db import models
from django.db.models import Q


class ServiceProvider(models.Model):
    """
    A business or person who does work for the estates (plumbing,
    security, cleaning...). Providers are never deleted: one that
    shouldn't be used again is blacklisted, which keeps the record and
    the reason.
    """

    class Category(models.TextChoices):
        PLUMBING = "PLUMBING", "Plumbing"
        ELECTRICAL = "ELECTRICAL", "Electrical"
        CONSTRUCTION = "CONSTRUCTION", "Construction"
        SECURITY = "SECURITY", "Security"
        CLEANING = "CLEANING", "Cleaning"
        LANDSCAPING = "LANDSCAPING", "Landscaping"
        PEST_CONTROL = "PEST_CONTROL", "Pest control"
        WASTE = "WASTE", "Waste collection"
        WATER = "WATER", "Water supply"
        SURVEYING = "SURVEYING", "Surveying"
        LEGAL = "LEGAL", "Legal"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        BLACKLISTED = "BLACKLISTED", "Blacklisted"

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)
    description = models.TextField(blank=True)

    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    website = models.URLField(blank=True)

    # Kenya Revenue Authority PIN and business registration number.
    kra_pin = models.CharField(max_length=20, blank=True)
    registration_number = models.CharField(max_length=50, blank=True)

    # The estates this provider works in. Empty means any estate.
    estates = models.ManyToManyField(
        "estates.Estate",
        related_name="service_providers",
        blank=True,
    )

    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    # The current blacklisting, if any. Full history is in StatusChange.
    blacklist_reason = models.TextField(blank=True)
    blacklisted_at = models.DateTimeField(null=True, blank=True)
    blacklisted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="service_providers_added",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kra_pin"],
                condition=~Q(kra_pin=""),
                name="unique_provider_kra_pin",
            ),
        ]

    def __str__(self):
        return self.name


class StatusChange(models.Model):
    """
    Every blacklisting and reinstatement, with who did it and why.
    """

    class Action(models.TextChoices):
        BLACKLISTED = "BLACKLISTED", "Blacklisted"
        REINSTATED = "REINSTATED", "Reinstated"

    provider = models.ForeignKey(
        ServiceProvider,
        on_delete=models.CASCADE,
        related_name="status_changes",
    )

    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.TextField()

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.provider} {self.action.lower()}"
