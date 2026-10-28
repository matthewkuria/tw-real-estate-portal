from django.contrib import admin

from .models import HousePhoto, RentalHouse, Tenant


class TenantInline(admin.TabularInline):
    model = Tenant
    fields = (
        "full_name",
        "phone",
        "email",
        "move_in_date",
    )
    extra = 0


class HousePhotoInline(admin.TabularInline):
    model = HousePhoto
    fields = (
        "image",
        "caption",
    )
    extra = 0


@admin.register(RentalHouse)
class RentalHouseAdmin(admin.ModelAdmin):
    list_display = (
        "house_number",
        "property",
        "house_type",
        "bedrooms",
    )

    list_filter = (
        "house_type",
        "property__estate",
    )

    search_fields = (
        "house_number",
        "property__plot_number",
        "tenants__full_name",
    )

    raw_id_fields = ("property",)

    inlines = [TenantInline, HousePhotoInline]


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "house",
        "move_in_date",
        "user",
    )

    list_filter = ("house__property__estate",)

    search_fields = (
        "full_name",
        "phone",
        "email",
        "id_number",
    )

    raw_id_fields = (
        "house",
        "user",
    )
