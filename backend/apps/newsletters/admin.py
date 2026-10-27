from django.contrib import admin

from .models import Delivery, Newsletter, Subscriber


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "audience",
        "estate",
        "status",
        "scheduled_for",
        "sent_at",
    )

    list_filter = (
        "status",
        "audience",
    )

    search_fields = ("subject",)

    readonly_fields = (
        "status",
        "sent_at",
        "sent_by",
        "created_by",
    )


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "user",
        "status",
        "unsubscribed_at",
    )

    list_filter = ("status",)

    search_fields = ("email",)

    raw_id_fields = ("user",)

    readonly_fields = ("token",)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "newsletter",
        "subscriber",
        "created_at",
    )

    list_filter = ("newsletter",)

    raw_id_fields = (
        "newsletter",
        "subscriber",
        "email",
    )
