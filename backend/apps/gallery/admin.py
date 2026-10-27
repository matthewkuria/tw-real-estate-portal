from django.contrib import admin
from django.utils.html import format_html

from .models import GalleryImage


@admin.register(GalleryImage)
class GalleryImageAdmin(admin.ModelAdmin):
    list_display = (
        "preview",
        "title",
        "estate",
        "is_published",
        "display_order",
        "created_at",
    )

    list_editable = (
        "is_published",
        "display_order",
    )

    list_filter = (
        "is_published",
        "estate",
    )

    search_fields = (
        "title",
        "caption",
    )

    readonly_fields = (
        "preview",
        "width",
        "height",
        "uploaded_by",
    )

    @admin.display(description="Preview")
    def preview(self, obj):
        image = obj.thumbnail or obj.image

        if not image:
            return "-"

        return format_html('<img src="{}" style="height: 60px;">', image.url)
