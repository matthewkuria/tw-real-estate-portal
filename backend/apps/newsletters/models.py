import uuid

from django.conf import settings
from django.db import models


class Subscriber(models.Model):
    """
    One email address on the newsletter list.

    For now every subscriber is a portal member (`user` is set), added
    automatically the first time a newsletter is sent to them. `user` is
    nullable so public website sign-ups can be added later without
    changing this table.
    """

    class Status(models.TextChoices):
        SUBSCRIBED = "SUBSCRIBED", "Subscribed"
        UNSUBSCRIBED = "UNSUBSCRIBED", "Unsubscribed"

    email = models.EmailField(unique=True)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="newsletter_subscription",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUBSCRIBED,
        db_index=True,
    )

    # Secret used in unsubscribe links, so no sign-in is needed.
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.email} ({self.status})"


class Newsletter(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SCHEDULED = "SCHEDULED", "Scheduled"
        SENT = "SENT", "Sent"

    class Audience(models.TextChoices):
        ALL_MEMBERS = "ALL_MEMBERS", "All members"
        ESTATE_MEMBERS = "ESTATE_MEMBERS", "Members of one estate"
        STAFF = "STAFF", "Staff"
        EVERYONE = "EVERYONE", "Members and staff"

    subject = models.CharField(max_length=200)

    # Shown by most inboxes next to the subject.
    preheader = models.CharField(max_length=200, blank=True)

    # Sanitised HTML from the editor; the plain-text version is generated.
    body_html = models.TextField()

    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ALL_MEMBERS,
    )

    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.PROTECT,
        related_name="newsletters",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="newsletters_created",
        null=True,
    )

    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="newsletters_sent",
        null=True,
        blank=True,
    )

    sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.subject


class Delivery(models.Model):
    """
    One newsletter going to one subscriber. The unique constraint makes
    a double send impossible even if "send" is clicked twice.
    """

    newsletter = models.ForeignKey(
        Newsletter,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )

    email = models.OneToOneField(
        "notifications.EmailOutbox",
        on_delete=models.SET_NULL,
        related_name="newsletter_delivery",
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["newsletter", "subscriber"],
                name="one_delivery_per_subscriber",
            ),
        ]

    def __str__(self):
        return f"{self.newsletter} to {self.subscriber.email}"
