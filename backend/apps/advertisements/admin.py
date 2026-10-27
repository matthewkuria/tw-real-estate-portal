from django.contrib import admin
from django.utils.html import format_html

from .models import Advertisement


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = (
        "preview",
        "title",
        "category",
        "visibility",
        "is_published",
        "is_featured",
        "publish_at",
        "expires_at",
    )

    list_editable = (
        "is_published",
        "is_featured",
    )

    list_filter = (
        "category",
        "visibility",
        "is_published",
        "is_featured",
        "estate",
    )

    search_fields = (
        "title",
        "summary",
        "description",
    )

    readonly_fields = (
        "preview",
        "image_width",
        "image_height",
        "created_by",
    )

    @admin.display(description="Image")
    def preview(self, obj):
        image = obj.thumbnail or obj.image

        if not image:
            return "-"

        return format_html('<img src="{}" style="height: 48px;">', image.url)
