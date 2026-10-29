import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _random_path(folder, filename):
    extension = os.path.splitext(filename)[1].lower()
    return f"listings/{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"


def listing_photo_path(instance, filename):
    return _random_path("photos", filename)


def listing_thumbnail_path(instance, filename):
    return _random_path("thumbnails", filename)


class PropertyListing(models.Model):
    """
    A property advertised for sale or rent. It can stand on its own or be
    tied to an estate (and one of its plots) when it's inside one.
    """

    class ListingType(models.TextChoices):
        SALE = "SALE", "For sale"
        RENT = "RENT", "For rent"

    class PropertyType(models.TextChoices):
        LAND = "LAND", "Land / plot"
        HOUSE = "HOUSE", "House"
        APARTMENT = "APARTMENT", "Apartment"
        COMMERCIAL = "COMMERCIAL", "Commercial"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        UNDER_OFFER = "UNDER_OFFER", "Under offer"
        SOLD = "SOLD", "Sold"
        RENTED = "RENTED", "Rented"

    class PricePeriod(models.TextChoices):
        MONTH = "MONTH", "Per month"
        YEAR = "YEAR", "Per year"

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    listing_type = models.CharField(max_length=10, choices=ListingType.choices, db_index=True)
    property_type = models.CharField(max_length=20, choices=PropertyType.choices, db_index=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
        db_index=True,
    )

    price = models.DecimalField(max_digits=14, decimal_places=2)

    # Rent only: what the price covers. Empty for sales.
    price_period = models.CharField(max_length=10, choices=PricePeriod.choices, blank=True)

    location = models.CharField(max_length=255)
    county = models.CharField(max_length=100, blank=True)

    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.SET_NULL,
        related_name="listings",
        null=True,
        blank=True,
    )
    plot = models.ForeignKey(
        "properties.Property",
        on_delete=models.SET_NULL,
        related_name="listings",
        null=True,
        blank=True,
    )

    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    floor_area_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    land_size_acres = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    # Short selling points, e.g. ["Borehole", "Gated", "Title deed ready"].
    features = models.JSONField(default=list, blank=True)

    contact_name = models.CharField(max_length=150, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)

    is_published = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_featured", "-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gt=0),
                name="listing_price_positive",
            ),
        ]

    def __str__(self):
        return self.title


class ListingPhoto(models.Model):
    listing = models.ForeignKey(
        PropertyListing,
        on_delete=models.CASCADE,
        related_name="photos",
    )

    image = models.ImageField(
        upload_to=listing_photo_path,
        width_field="width",
        height_field="height",
    )
    thumbnail = models.ImageField(upload_to=listing_thumbnail_path, blank=True, editable=False)
    width = models.PositiveIntegerField(null=True, editable=False)
    height = models.PositiveIntegerField(null=True, editable=False)

    caption = models.CharField(max_length=150, blank=True)

    # Display order; the first photo is the cover.
    position = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"Photo of {self.listing}"
