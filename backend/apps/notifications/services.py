import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from apps.community.realtime import broadcast, user_group

from . import links
from .models import EmailOutbox, Notification, NotificationPreference
from .serializers import NotificationSerializer
from .types import EMAIL_POLICY, NotificationType


logger = logging.getLogger(__name__)

MAX_EMAIL_ATTEMPTS = 5

# A SENDING row older than this belongs to a worker that died mid-batch.
STALE_CLAIM_AFTER = timedelta(minutes=15)


# ============================================================================
# CREATING NOTIFICATIONS
# ============================================================================

def notify(recipients, type, title, body="", link="", actor=None, data=None):
    """
    Notify each recipient in-app, push it to their open connections, and
    queue an email when the type and their preferences allow it. The actor
    is never notified about their own action.
    """
    seen = set()
    unique_recipients = []

    for user in recipients:
        if user.pk in seen or (actor is not None and user.pk == actor.pk):
            continue

        seen.add(user.pk)
        unique_recipients.append(user)

    if not unique_recipients:
        return []

    notifications = Notification.objects.bulk_create(
        [
            Notification(
                recipient=user,
                type=type,
                title=title,
                body=body,
                link=link,
                actor=actor,
                data=data or {},
            )
            for user in unique_recipients
        ]
    )

    for notification in notifications:
        _push(notification, "notification.created")

    _queue_emails(type, notifications)

    return notifications


def notify_grouped(recipient, type, group_key, title, body, link="", actor=None, data=None):
    """
    Like notify() for one recipient, but folds repeat events into their
    existing unread notification with the same group key, so a busy thread
    produces one "N new messages" item rather than N items.

    `title` and `body` are callables taking the running count.
    """
    if actor is not None and recipient.pk == actor.pk:
        return None

    existing = (
        Notification.objects
        .select_for_update()
        .filter(
            recipient=recipient,
            type=type,
            read_at__isnull=True,
            data__group=group_key,
        )
        .first()
    )

    if existing is None:
        payload = {**(data or {}), "group": group_key, "count": 1}
        created = notify(
            [recipient],
            type,
            title(1),
            body(1),
            link,
            actor,
            payload,
        )
        return created[0] if created else None

    count = existing.data.get("count", 1) + 1
    existing.data = {**existing.data, **(data or {}), "count": count}
    existing.title = title(count)
    existing.body = body(count)
    existing.actor = actor
    existing.created_at = timezone.now()
    existing.save(update_fields=["data", "title", "body", "actor", "created_at"])

    _push(existing, "notification.updated")

    return existing


def _push(notification, event_type):
    broadcast(
        [user_group(notification.recipient_id)],
        event_type,
        NotificationSerializer(notification).data,
    )


# ============================================================================
# READING
# ============================================================================

def unread_count(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def mark_read(user, notification_ids=None):
    """
    Mark some (or, with no ids, all) of a user's notifications as read.
    Returns how many changed. Other open tabs are told so their badge
    stays in sync.
    """
    queryset = Notification.objects.filter(recipient=user, read_at__isnull=True)

    if notification_ids is not None:
        queryset = queryset.filter(pk__in=notification_ids)

    ids = list(queryset.values_list("pk", flat=True))

    if not ids:
        return 0

    Notification.objects.filter(pk__in=ids).update(read_at=timezone.now())

    broadcast(
        [user_group(user.pk)],
        "notification.read",
        {"ids": ids, "unread_count": unread_count(user)},
    )

    return len(ids)


# ============================================================================
# PREFERENCES
# ============================================================================

def preferences_for(user):
    stored = dict(
        NotificationPreference.objects
        .filter(user=user)
        .values_list("type", "email_enabled")
    )

    return [
        {
            "type": notification_type.value,
            "label": notification_type.label,
            "sends_email": policy.sends_email,
            "can_opt_out": policy.can_opt_out,
            "email_enabled": policy.sends_email and stored.get(notification_type.value, True),
        }
        for notification_type, policy in EMAIL_POLICY.items()
    ]


@transaction.atomic
def set_preferences(user, changes):
    for change in changes:
        NotificationPreference.objects.update_or_create(
            user=user,
            type=change["type"],
            defaults={"email_enabled": change["email_enabled"]},
        )


# ============================================================================
# EMAIL
# ============================================================================

def _queue_emails(type, notifications):
    policy = EMAIL_POLICY[NotificationType(type)]

    if not policy.sends_email:
        return

    opted_out = set()

    if policy.can_opt_out:
        opted_out = set(
            NotificationPreference.objects
            .filter(
                user_id__in=[n.recipient_id for n in notifications],
                type=type,
                email_enabled=False,
            )
            .values_list("user_id", flat=True)
        )

    EmailOutbox.objects.bulk_create(
        [
            EmailOutbox(
                notification=notification,
                to_email=notification.recipient.email,
                subject=notification.title,
                body=_email_body(notification, policy.can_opt_out),
            )
            for notification in notifications
            if notification.recipient_id not in opted_out
            and notification.recipient.email
        ]
    )


def _email_body(notification, can_opt_out):
    parts = []

    if notification.body:
        parts.append(notification.body)

    if notification.link:
        parts.append(f"Open in the portal:\n{settings.FRONTEND_URL}{notification.link}")

    if can_opt_out:
        parts.append(
            "You can turn these emails off in your notification settings:\n"
            f"{settings.FRONTEND_URL}{links.PREFERENCES}"
        )

    return "\n\n".join(parts)


def deliver(email):
    """
    Hand one outbox row to the mail backend.
    """
    message = EmailMultiAlternatives(
        subject=email.subject,
        body=email.body,
        from_email=None,
        to=[email.to_email],
        headers=email.headers or None,
    )

    if email.html_body:
        message.attach_alternative(email.html_body, "text/html")

    message.send()


def send_pending_emails(batch_size=100, max_attempts=MAX_EMAIL_ATTEMPTS):
    """
    Send one batch from the outbox, transactional emails first. Failures
    are retried with exponential backoff (2, 4, 8... minutes) and marked
    FAILED after `max_attempts`. Safe to run from several workers at once
    on Postgres.

    Returns (sent, failed) counts for this batch.
    """
    now = timezone.now()

    # Recover rows claimed by a worker that crashed mid-send.
    EmailOutbox.objects.filter(
        status=EmailOutbox.Status.SENDING,
        claimed_at__lt=now - STALE_CLAIM_AFTER,
    ).update(status=EmailOutbox.Status.PENDING)

    with transaction.atomic():
        ids = list(
            EmailOutbox.objects
            .select_for_update(skip_locked=True)
            .filter(status=EmailOutbox.Status.PENDING, next_attempt_at__lte=now)
            .order_by("priority", "id")
            .values_list("pk", flat=True)[:batch_size]
        )
        EmailOutbox.objects.filter(pk__in=ids).update(
            status=EmailOutbox.Status.SENDING,
            claimed_at=now,
        )

    sent = failed = 0

    for email in EmailOutbox.objects.filter(pk__in=ids).order_by("priority", "id"):
        try:
            deliver(email)
        except Exception as exc:
            email.attempts += 1
            email.last_error = f"{type(exc).__name__}: {exc}"

            if email.attempts >= max_attempts:
                email.status = EmailOutbox.Status.FAILED
                failed += 1
                logger.error("Giving up on email %s: %s", email.pk, email.last_error)
            else:
                email.status = EmailOutbox.Status.PENDING
                email.next_attempt_at = timezone.now() + timedelta(minutes=2 ** email.attempts)

            email.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])
            continue

        email.status = EmailOutbox.Status.SENT
        email.sent_at = timezone.now()
        email.save(update_fields=["status", "sent_at"])
        sent += 1

    return sent, failed
