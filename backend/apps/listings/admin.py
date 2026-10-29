from django.contrib import admin

from .models import ListingPhoto, PropertyListing


class ListingPhotoInline(admin.TabularInline):
    model = ListingPhoto
    fields = (
        "image",
        "caption",
        "position",
    )
    extra = 0


@admin.register(PropertyListing)
class PropertyListingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "listing_type",
        "property_type",
        "status",
        "price",
        "location",
        "is_published",
        "is_featured",
    )

    list_filter = (
        "listing_type",
        "property_type",
        "status",
        "is_published",
        "estate",
    )

    search_fields = (
        "title",
        "location",
        "county",
    )

    raw_id_fields = (
        "estate",
        "plot",
    )

    inlines = [ListingPhotoInline]
