from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.models import EmailOutbox

from .content import to_text
from .models import Delivery, Newsletter, Subscriber


class NotEditable(Exception):
    pass


class CannotSend(Exception):
    pass


# ============================================================================
# AUDIENCE
# ============================================================================

def audience_users(newsletter):
    users = User.objects.filter(is_active=True, status=User.Status.ACTIVE)
    audience = newsletter.audience

    if audience == Newsletter.Audience.ALL_MEMBERS:
        return users.filter(role=User.Role.MEMBER)

    if audience == Newsletter.Audience.ESTATE_MEMBERS:
        return users.filter(
            role=User.Role.MEMBER,
            properties__estate_id=newsletter.estate_id,
        ).distinct()

    if audience == Newsletter.Audience.STAFF:
        return users.exclude(role=User.Role.MEMBER)

    return users


def _subscribers_for(users):
    """
    Subscriber rows for these users, creating any that are missing
    (members are subscribed by default) and keeping emails in step with
    the account. Unsubscribed people are left out.
    """
    users = list(users)
    existing = {
        s.user_id: s
        for s in Subscriber.objects.filter(user__in=users)
    }

    missing = [u for u in users if u.pk not in existing]
    Subscriber.objects.bulk_create(
        [Subscriber(user=u, email=u.email) for u in missing],
        ignore_conflicts=True,
    )

    subscribers = list(
        Subscriber.objects
        .filter(user__in=users)
        .select_related("user")
    )

    for subscriber in subscribers:
        if subscriber.email != subscriber.user.email:
            subscriber.email = subscriber.user.email
            subscriber.save(update_fields=["email", "updated_at"])

    return [s for s in subscribers if s.status == Subscriber.Status.SUBSCRIBED]


def recipient_count(newsletter):
    """
    How many people a send would reach right now.
    """
    users = audience_users(newsletter)

    return (
        users.count()
        - Subscriber.objects.filter(
            user__in=users,
            status=Subscriber.Status.UNSUBSCRIBED,
        ).count()
    )


# ============================================================================
# RENDERING
# ============================================================================

def unsubscribe_api_url(token):
    path = reverse("newsletters:unsubscribe", args=[token])
    return f"{settings.API_BASE_URL}{path}"


def unsubscribe_page_url(token):
    return f"{settings.FRONTEND_URL}/newsletter/unsubscribe?token={token}"


def archive_url(newsletter):
    return f"{settings.FRONTEND_URL}/newsletters/{newsletter.pk}"


def render(newsletter, token=None):
    """
    The HTML and text bodies for one recipient. Each copy has its own
    unsubscribe link, so every email is rendered separately.
    """
    context = {
        "subject": newsletter.subject,
        "preheader": newsletter.preheader,
        "body_html": newsletter.body_html,
        "body_text": to_text(newsletter.body_html),
        "site_name": settings.SITE_NAME,
        "unsubscribe_url": (
            unsubscribe_page_url(token) if token else f"{settings.FRONTEND_URL}/settings/notifications"
        ),
        "archive_url": archive_url(newsletter),
    }

    return (
        render_to_string("newsletters/email.html", context),
        render_to_string("newsletters/email.txt", context).strip(),
    )


def _unsubscribe_headers(token):
    # RFC 8058 one-click unsubscribe, which Gmail and Yahoo expect for
    # bulk mail: the inbox POSTs to this URL directly.
    return {
        "List-Unsubscribe": f"<{unsubscribe_api_url(token)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


# ============================================================================
# EDITING AND SENDING
# ============================================================================

def ensure_editable(newsletter):
    if newsletter.status == Newsletter.Status.SENT:
        raise NotEditable("A sent newsletter can't be changed.")


def send_test(newsletter, emails):
    """
    Queue a copy for each address, marked as a test. Nothing is recorded
    as a delivery, and the unsubscribe link goes to settings instead.
    """
    html_body, text_body = render(newsletter)

    EmailOutbox.objects.bulk_create(
        [
            EmailOutbox(
                to_email=email,
                subject=f"[Test] {newsletter.subject}",
                body=text_body,
                html_body=html_body,
                priority=EmailOutbox.Priority.TRANSACTIONAL,
            )
            for email in emails
        ]
    )


@transaction.atomic
def schedule(newsletter, send_at, user):
    newsletter = Newsletter.objects.select_for_update().get(pk=newsletter.pk)
    ensure_editable(newsletter)

    newsletter.status = Newsletter.Status.SCHEDULED
    newsletter.scheduled_for = send_at
    newsletter.sent_by = user
    newsletter.save(update_fields=["status", "scheduled_for", "sent_by", "updated_at"])

    return newsletter


@transaction.atomic
def cancel_schedule(newsletter):
    newsletter = Newsletter.objects.select_for_update().get(pk=newsletter.pk)

    if newsletter.status != Newsletter.Status.SCHEDULED:
        raise NotEditable("Only a scheduled newsletter can be cancelled.")

    newsletter.status = Newsletter.Status.DRAFT
    newsletter.scheduled_for = None
    newsletter.save(update_fields=["status", "scheduled_for", "updated_at"])

    return newsletter


@transaction.atomic
def send_now(newsletter, user=None):
    """
    Queue one email per subscribed recipient. The row lock and the
    status check make a second click (or a second worker) a no-op.
    """
    newsletter = Newsletter.objects.select_for_update().get(pk=newsletter.pk)
    ensure_editable(newsletter)

    subscribers = _subscribers_for(audience_users(newsletter))

    if not subscribers:
        raise CannotSend("No subscribed recipients in this audience.")

    rendered = [(s, render(newsletter, s.token)) for s in subscribers]

    emails = EmailOutbox.objects.bulk_create(
        [
            EmailOutbox(
                to_email=subscriber.email,
                subject=newsletter.subject,
                body=text_body,
                html_body=html_body,
                headers=_unsubscribe_headers(subscriber.token),
                priority=EmailOutbox.Priority.BULK,
            )
            for subscriber, (html_body, text_body) in rendered
        ]
    )

    Delivery.objects.bulk_create(
        [
            Delivery(newsletter=newsletter, subscriber=subscriber, email=email)
            for (subscriber, _), email in zip(rendered, emails)
        ]
    )

    newsletter.status = Newsletter.Status.SENT
    newsletter.sent_at = timezone.now()
    newsletter.sent_by = user or newsletter.sent_by
    newsletter.save(update_fields=["status", "sent_at", "sent_by", "updated_at"])

    return newsletter


def release_due():
    """
    Send scheduled newsletters whose time has come. Called by the email
    worker (`manage.py send_notifications`) before each batch.
    """
    due = Newsletter.objects.filter(
        status=Newsletter.Status.SCHEDULED,
        scheduled_for__lte=timezone.now(),
    ).values_list("pk", flat=True)

    released = 0

    for newsletter_id in list(due):
        try:
            send_now(Newsletter(pk=newsletter_id))
            released += 1
        except (NotEditable, CannotSend):
            # Already sent by another worker, or nobody left to send to.
            Newsletter.objects.filter(
                pk=newsletter_id,
                status=Newsletter.Status.SCHEDULED,
            ).update(status=Newsletter.Status.DRAFT, scheduled_for=None)

    return released


def delivery_stats(newsletter):
    counts = newsletter.deliveries.aggregate(
        recipients=Count("pk"),
        sent=Count("pk", filter=Q(email__status=EmailOutbox.Status.SENT)),
        failed=Count("pk", filter=Q(email__status=EmailOutbox.Status.FAILED)),
    )
    counts["pending"] = counts["recipients"] - counts["sent"] - counts["failed"]

    return counts


# ============================================================================
# SUBSCRIPTIONS
# ============================================================================

def subscription_for(user):
    subscriber, _ = Subscriber.objects.get_or_create(
        user=user,
        defaults={"email": user.email},
    )
    return subscriber


def set_subscribed(subscriber, subscribed):
    if subscribed:
        subscriber.status = Subscriber.Status.SUBSCRIBED
        subscriber.unsubscribed_at = None
    elif subscriber.status != Subscriber.Status.UNSUBSCRIBED:
        subscriber.status = Subscriber.Status.UNSUBSCRIBED
        subscriber.unsubscribed_at = timezone.now()

    subscriber.save(update_fields=["status", "unsubscribed_at", "updated_at"])

    return subscriber
