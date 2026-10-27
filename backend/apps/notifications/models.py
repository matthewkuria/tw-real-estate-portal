from django.conf import settings
from django.db import models
from django.utils import timezone

from .types import NotificationType


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    type = models.CharField(max_length=40, choices=NotificationType.choices)

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)

    # Path in the Next.js app, e.g. /community/threads/12
    link = models.CharField(max_length=300, blank=True)

    # Who caused it, if anyone (e.g. the person who replied).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )

    # Ids of the related objects, e.g. {"thread": 12, "message": 40}.
    data = models.JSONField(default=dict, blank=True)

    read_at = models.DateTimeField(null=True, blank=True)

    # Not auto_now_add: grouped thread notifications bump this when a new
    # message is folded in.
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "read_at"]),
            models.Index(fields=["recipient", "-created_at"]),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def __str__(self):
        return f"{self.get_type_display()} for {self.recipient}"


class NotificationPreference(models.Model):
    """
    A user's email choice for one notification type. Only stored when the
    user changes it; otherwise the type's default applies.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )

    type = models.CharField(max_length=40, choices=NotificationType.choices)
    email_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "type"],
                name="unique_notification_preference",
            ),
        ]

    def __str__(self):
        state = "on" if self.email_enabled else "off"
        return f"{self.user} {self.type} email {state}"


class EmailOutbox(models.Model):
    """
    Emails waiting to be sent by `manage.py send_notifications`. Used by
    notifications and newsletters alike.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    class Priority(models.IntegerChoices):
        # Lower sends first, so a newsletter to every member never holds
        # up an "account approved" email queued behind it.
        TRANSACTIONAL = 0, "Transactional"
        BULK = 10, "Bulk"

    notification = models.ForeignKey(
        Notification,
        on_delete=models.SET_NULL,
        related_name="emails",
        null=True,
        blank=True,
    )

    to_email = models.EmailField()
    subject = models.CharField(max_length=255)

    # Plain text is always sent; HTML, when present, is the rich version.
    body = models.TextField()
    html_body = models.TextField(blank=True)

    # Extra headers, e.g. List-Unsubscribe for newsletters.
    headers = models.JSONField(default=dict, blank=True)

    priority = models.PositiveSmallIntegerField(
        choices=Priority.choices,
        default=Priority.TRANSACTIONAL,
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)

    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "email outbox"
        indexes = [
            models.Index(fields=["status", "priority", "next_attempt_at"]),
        ]

    def __str__(self):
        return f"{self.subject} to {self.to_email} ({self.status})"
