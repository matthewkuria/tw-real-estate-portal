from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("member", "estate", "property", "amount", "method", "reference", "status", "received_at")
    list_filter = ("status", "method", "estate")
    search_fields = ("reference", "member__email", "member__first_name", "member__last_name")
    autocomplete_fields = ()
    raw_id_fields = ("member", "property", "recorded_by", "confirmed_by", "status_changed_by")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "received_at"
