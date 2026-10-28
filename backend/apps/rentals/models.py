import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _random_path(folder, filename):
    extension = os.path.splitext(filename)[1].lower()
    return f"rentals/{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"


def house_photo_path(instance, filename):
    return _random_path("photos", filename)


def house_thumbnail_path(instance, filename):
    return _random_path("thumbnails", filename)


class RentalHouse(models.Model):
    """
    A house that's rented out, built on a member's plot. The landlord is
    whoever the plot is allocated to.
    """

    class HouseType(models.TextChoices):
        BUNGALOW = "BUNGALOW", "Bungalow"
        MAISONETTE = "MAISONETTE", "Maisonette"
        TOWNHOUSE = "TOWNHOUSE", "Townhouse"
        APARTMENT = "APARTMENT", "Apartment unit"
        BEDSITTER = "BEDSITTER", "Bedsitter"
        SINGLE_ROOM = "SINGLE_ROOM", "Single room"
        OTHER = "OTHER", "Other"

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.PROTECT,
        related_name="rental_houses",
    )

    # How the house is known on the plot, e.g. "A3" or "Mwangi Villa".
    house_number = models.CharField(max_length=50)

    house_type = models.CharField(max_length=20, choices=HouseType.choices)

    # Empty when not recorded; 0 for a bedsitter or single room.
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)

    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["property__estate__name", "property__plot_number", "house_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "house_number"],
                name="unique_house_number_per_plot",
            ),
        ]

    def __str__(self):
        return f"{self.property} - {self.house_number}"


class HousePhoto(models.Model):
    house = models.ForeignKey(
        RentalHouse,
        on_delete=models.CASCADE,
        related_name="photos",
    )

    image = models.ImageField(
        upload_to=house_photo_path,
        width_field="width",
        height_field="height",
    )
    thumbnail = models.ImageField(upload_to=house_thumbnail_path, blank=True, editable=False)
    width = models.PositiveIntegerField(null=True, editable=False)
    height = models.PositiveIntegerField(null=True, editable=False)

    caption = models.CharField(max_length=150, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"Photo of {self.house}"


class Tenant(models.Model):
    """
    Someone living in a rented house. A contact record first; it can be
    linked to a portal account if the tenant also has one.
    """

    house = models.ForeignKey(
        RentalHouse,
        on_delete=models.PROTECT,
        related_name="tenants",
    )

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)

    # National ID or passport number.
    id_number = models.CharField(max_length=30, blank=True)

    move_in_date = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="tenancy",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "id"]

    def __str__(self):
        return self.full_name
