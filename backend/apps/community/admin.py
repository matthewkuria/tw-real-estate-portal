from django.contrib import admin

from .models import Message, Reaction, Thread, Update


@admin.register(Update)
class UpdateAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "estate",
        "author",
        "is_pinned",
        "created_at",
    )

    list_filter = (
        "is_pinned",
        "estate",
    )

    search_fields = (
        "title",
        "body",
    )

    ordering = ("-created_at",)


class MessageInline(admin.TabularInline):
    model = Message
    fields = (
        "author",
        "body",
        "is_deleted",
        "created_at",
    )
    readonly_fields = ("created_at",)
    extra = 0


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "estate",
        "author",
        "is_pinned",
        "is_locked",
        "last_message_at",
    )

    list_filter = (
        "is_pinned",
        "is_locked",
        "estate",
    )

    search_fields = ("title",)

    ordering = ("-last_message_at",)

    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "thread",
        "author",
        "is_deleted",
        "created_at",
    )

    list_filter = ("is_deleted",)

    search_fields = (
        "body",
        "author__email",
    )

    ordering = ("-created_at",)


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = (
        "message",
        "user",
        "emoji",
        "created_at",
    )

    list_filter = ("emoji",)
