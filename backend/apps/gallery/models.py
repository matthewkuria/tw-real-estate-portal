import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _random_path(folder, filename):
    """
    Random file names, so uploads never collide and original file names
    (which can contain personal details) aren't published.
    """
    extension = os.path.splitext(filename)[1].lower()
    return f"gallery/{folder}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"


def gallery_image_path(instance, filename):
    return _random_path("images", filename)


def gallery_thumbnail_path(instance, filename):
    return _random_path("thumbnails", filename)


class GalleryImage(models.Model):
    title = models.CharField(max_length=150)
    caption = models.TextField(blank=True)

    # Read by screen readers; falls back to the title when empty.
    alt_text = models.CharField(max_length=250, blank=True)

    image = models.ImageField(
        upload_to=gallery_image_path,
        width_field="width",
        height_field="height",
    )

    # Generated on upload, for grids and previews.
    thumbnail = models.ImageField(
        upload_to=gallery_thumbnail_path,
        blank=True,
        editable=False,
    )

    width = models.PositiveIntegerField(null=True, editable=False)
    height = models.PositiveIntegerField(null=True, editable=False)

    # Optional: lets the website show an estate's own photos.
    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.SET_NULL,
        related_name="gallery_images",
        null=True,
        blank=True,
    )

    # Unpublished images are only visible to admins.
    is_published = models.BooleanField(default=True, db_index=True)

    # Lower numbers show first.
    display_order = models.PositiveIntegerField(default=0, db_index=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="gallery_uploads",
        null=True,
        editable=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "-created_at", "-id"]

    def __str__(self):
        return self.title
