"""
What happens in the portal, turned into notifications for the right
people. Call these inside the transaction that made the change, so a
rollback discards the notifications too.
"""

from django.db.models import Q
from django.utils.text import Truncator

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole

from . import links
from .services import notify, notify_grouped
from .types import NotificationType as T


# Staff who can approve registrations.
APPROVER_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.MANAGER,
}


def _preview(text, length=140):
    return Truncator(text).chars(length)


def _name(user):
    if user is None:
        return "Someone"

    return user.get_full_name() or "A member"


def _active_users():
    return User.objects.filter(is_active=True, status=User.Status.ACTIVE)


# ============================================================================
# COMMUNITY
# ============================================================================

def update_posted(update):
    """
    Everyone who can see the update: all active users, or for an estate
    update, staff plus members with a plot in that estate.
    """
    recipients = _active_users()

    if update.estate_id:
        recipients = recipients.filter(
            Q(role__in=IsStaffRole.STAFF_ROLES)
            | Q(properties__estate_id=update.estate_id)
        ).distinct()

    notify(
        recipients,
        T.UPDATE_POSTED,
        title=f"New update: {update.title}",
        body=_preview(update.body, 300),
        link=links.update(update.pk),
        actor=update.author,
        data={"update": update.pk},
    )


def message_posted(message):
    """
    The author of the message being replied to gets a reply notification.
    The thread's starter gets one grouped "new messages" notification
    (unless they were just notified as the reply target).
    """
    from apps.community.access import can_view_estate

    thread = message.thread
    author = message.author
    notified = set()

    reply_to = message.reply_to

    if reply_to is not None and reply_to.author is not None and not reply_to.is_deleted:
        notify(
            [reply_to.author],
            T.FORUM_REPLY,
            title=f"{_name(author)} replied to your message",
            body=_preview(message.body),
            link=links.message(thread.pk, message.pk),
            actor=author,
            data={"thread": thread.pk, "message": message.pk},
        )
        notified.add(reply_to.author_id)

    starter = thread.author

    if (
        starter is not None
        and starter.pk not in notified
        and starter.is_active
        and can_view_estate(starter, thread.estate_id)
    ):
        notify_grouped(
            starter,
            T.FORUM_THREAD_ACTIVITY,
            group_key=f"thread:{thread.pk}",
            title=lambda count: (
                f"New message in “{thread.title}”" if count == 1
                else f"{count} new messages in “{thread.title}”"
            ),
            body=lambda count: f"{_name(author)}: {_preview(message.body)}",
            link=links.message(thread.pk, message.pk),
            actor=author,
            data={"thread": thread.pk, "message": message.pk},
        )


def reaction_added(reaction):
    message = reaction.message

    if message.author is None or message.is_deleted:
        return

    notify(
        [message.author],
        T.FORUM_REACTION,
        title=f"{_name(reaction.user)} reacted {reaction.get_emoji_display()} to your message",
        body=_preview(message.body),
        link=links.message(message.thread_id, message.pk),
        actor=reaction.user,
        data={"thread": message.thread_id, "message": message.pk},
    )


# ============================================================================
# ACCOUNTS
# ============================================================================

def registration_pending(user):
    approvers = _active_users().filter(role__in=APPROVER_ROLES)

    notify(
        approvers,
        T.REGISTRATION_PENDING,
        title=f"New registration: {user.get_full_name() or user.email}",
        body=f"{user.email} registered and is waiting for approval.",
        link=links.member(user.pk),
        data={"user": user.pk},
    )


_ACCOUNT_MESSAGES = {
    T.ACCOUNT_APPROVED: (
        "Your account has been approved",
        "Welcome! Your membership has been approved and you can now sign in.",
    ),
    T.ACCOUNT_REJECTED: (
        "Your registration was not approved",
        "Your registration was reviewed and not approved. Please contact "
        "the office if you think this is a mistake.",
    ),
    T.ACCOUNT_SUSPENDED: (
        "Your account has been suspended",
        "Your account has been suspended and you can't sign in for now. "
        "Please contact the office for details.",
    ),
    T.ACCOUNT_REACTIVATED: (
        "Your account has been reactivated",
        "Your account is active again and you can sign in.",
    ),
}


def account_status_changed(user, previous_status):
    new_status = user.status

    if new_status == User.Status.ACTIVE:
        notification_type = (
            T.ACCOUNT_REACTIVATED
            if previous_status == User.Status.SUSPENDED
            else T.ACCOUNT_APPROVED
        )
    elif new_status == User.Status.REJECTED:
        notification_type = T.ACCOUNT_REJECTED
    elif new_status == User.Status.SUSPENDED:
        notification_type = T.ACCOUNT_SUSPENDED
    else:
        return

    title, body = _ACCOUNT_MESSAGES[notification_type]

    notify(
        [user],
        notification_type,
        title=title,
        body=body,
        link=links.ACCOUNT,
        data={"status": new_status},
    )


# ============================================================================
# PAYMENTS
# ============================================================================

def payment_status_changed(payment):
    from apps.payments.models import Payment

    types = {
        Payment.Status.CONFIRMED: (T.PAYMENT_CONFIRMED, "confirmed"),
        Payment.Status.REJECTED: (T.PAYMENT_REJECTED, "rejected"),
        Payment.Status.REVERSED: (T.PAYMENT_REVERSED, "reversed"),
    }

    if payment.status not in types:
        return

    notification_type, verb = types[payment.status]
    reference = f" (ref {payment.reference})" if payment.reference else ""

    notify(
        [payment.member],
        notification_type,
        title=f"Payment of KES {payment.amount:,.2f} {verb}",
        body=(
            f"Your {payment.get_method_display()} payment of "
            f"KES {payment.amount:,.2f}{reference} toward "
            f"{payment.estate} has been {verb}."
        ),
        link=links.payment(payment.pk),
        actor=payment.confirmed_by if payment.status == Payment.Status.CONFIRMED else None,
        data={"payment": payment.pk},
    )
