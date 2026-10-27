from django.contrib import admin

from .models import EmailOutbox, Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "recipient",
        "type",
        "read_at",
        "created_at",
    )

    list_filter = (
        "type",
        ("read_at", admin.EmptyFieldListFilter),
    )

    search_fields = (
        "title",
        "recipient__email",
    )

    raw_id_fields = (
        "recipient",
        "actor",
    )

    ordering = ("-created_at",)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "type",
        "email_enabled",
    )

    list_filter = (
        "type",
        "email_enabled",
    )

    search_fields = ("user__email",)

    raw_id_fields = ("user",)


@admin.register(EmailOutbox)
class EmailOutboxAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "to_email",
        "status",
        "attempts",
        "next_attempt_at",
        "sent_at",
    )

    list_filter = ("status",)

    search_fields = (
        "to_email",
        "subject",
    )

    readonly_fields = (
        "notification",
        "attempts",
        "last_error",
        "claimed_at",
        "sent_at",
        "created_at",
    )

    actions = ["retry_now"]

    ordering = ("-created_at",)

    @admin.action(description="Retry selected emails now")
    def retry_now(self, request, queryset):
        from django.utils import timezone

        updated = queryset.exclude(status=EmailOutbox.Status.SENT).update(
            status=EmailOutbox.Status.PENDING,
            attempts=0,
            next_attempt_at=timezone.now(),
        )
        self.message_user(request, f"{updated} email(s) queued for retry.")
