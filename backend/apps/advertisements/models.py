import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _random_path(folder, filename):
    extension = os.path.splitext(filename)[1].lower()
    return f"ads/{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"


def ad_image_path(instance, filename):
    return _random_path("images", filename)


def ad_thumbnail_path(instance, filename):
    return _random_path("thumbnails", filename)


class Advertisement(models.Model):
    class Category(models.TextChoices):
        EVENT = "EVENT", "Event"
        SERVICE = "SERVICE", "Service"
        ITEM = "ITEM", "Item for sale"
        OTHER = "OTHER", "Other"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public website and portal"
        MEMBERS = "MEMBERS", "Portal members only"

    category = models.CharField(
        max_length=10,
        choices=Category.choices,
        db_index=True,
    )

    title = models.CharField(max_length=150)

    # One line for cards and lists.
    summary = models.CharField(max_length=300, blank=True)

    # Plain text; line breaks are kept.
    description = models.TextField(blank=True)

    image = models.ImageField(
        upload_to=ad_image_path,
        width_field="image_width",
        height_field="image_height",
        blank=True,
    )
    thumbnail = models.ImageField(upload_to=ad_thumbnail_path, blank=True, editable=False)
    image_width = models.PositiveIntegerField(null=True, editable=False)
    image_height = models.PositiveIntegerField(null=True, editable=False)

    # Shown in KES. Empty when there's no price (e.g. a free event).
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Events.
    event_starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    event_ends_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)

    # Call to action, e.g. "Register" -> https://...
    link_url = models.URLField(blank=True)
    link_label = models.CharField(max_length=40, blank=True)

    contact_name = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)

    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
    )

    # Optional. On members-only ads it limits the audience to that
    # estate's plot holders (and staff); on public ads it's a tag for
    # filtering.
    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.SET_NULL,
        related_name="advertisements",
        null=True,
        blank=True,
    )

    # Unpublished ads are drafts, visible only to admins.
    is_published = models.BooleanField(default=True)

    # Featured ads sort first, e.g. for a banner slot.
    is_featured = models.BooleanField(default=False)

    # The ad shows between these times. An event ad with no end set
    # stops showing when the event is over.
    publish_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="advertisements",
        null=True,
        editable=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_featured", "-publish_at", "-id"]

    def __str__(self):
        return self.title
