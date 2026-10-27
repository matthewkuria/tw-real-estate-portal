from django.contrib import admin

from .models import ServiceProvider, StatusChange


class StatusChangeInline(admin.TabularInline):
    model = StatusChange
    fields = (
        "action",
        "reason",
        "changed_by",
        "created_at",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # Blacklist through the API so the reason and history stay
        # consistent.
        return False


@admin.register(ServiceProvider)
class ServiceProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "phone",
        "status",
        "blacklisted_at",
    )

    list_filter = (
        "status",
        "category",
        "estates",
    )

    search_fields = (
        "name",
        "contact_person",
        "phone",
        "email",
        "kra_pin",
    )

    filter_horizontal = ("estates",)

    readonly_fields = (
        "status",
        "blacklist_reason",
        "blacklisted_at",
        "blacklisted_by",
        "created_by",
    )

    inlines = [StatusChangeInline]
