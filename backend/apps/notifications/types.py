from dataclasses import dataclass

from django.db import models


class NotificationType(models.TextChoices):
    UPDATE_POSTED = "update_posted", "New community update"

    FORUM_REPLY = "forum_reply", "Reply to your message"
    FORUM_REACTION = "forum_reaction", "Reaction to your message"
    FORUM_THREAD_ACTIVITY = "forum_thread_activity", "New messages in your thread"

    REGISTRATION_PENDING = "registration_pending", "New registration awaiting approval"
    ACCOUNT_APPROVED = "account_approved", "Account approved"
    ACCOUNT_REJECTED = "account_rejected", "Registration rejected"
    ACCOUNT_SUSPENDED = "account_suspended", "Account suspended"
    ACCOUNT_REACTIVATED = "account_reactivated", "Account reactivated"

    PAYMENT_CONFIRMED = "payment_confirmed", "Payment confirmed"
    PAYMENT_REJECTED = "payment_rejected", "Payment rejected"
    PAYMENT_REVERSED = "payment_reversed", "Payment reversed"

    PLOT_ALLOCATED = "plot_allocated", "Plot allocated to you"
    PLOT_RELEASED = "plot_released", "Plot allocation ended"


@dataclass(frozen=True)
class EmailPolicy:
    sends_email: bool
    # Account emails can't be turned off: a rejected or suspended user
    # can't sign in to see an in-app notification.
    can_opt_out: bool = True


_IN_APP_ONLY = EmailPolicy(sends_email=False, can_opt_out=False)
_OPTIONAL_EMAIL = EmailPolicy(sends_email=True)
_REQUIRED_EMAIL = EmailPolicy(sends_email=True, can_opt_out=False)

T = NotificationType

EMAIL_POLICY = {
    T.UPDATE_POSTED: _OPTIONAL_EMAIL,
    T.FORUM_REPLY: _IN_APP_ONLY,
    T.FORUM_REACTION: _IN_APP_ONLY,
    T.FORUM_THREAD_ACTIVITY: _IN_APP_ONLY,
    T.REGISTRATION_PENDING: _OPTIONAL_EMAIL,
    T.ACCOUNT_APPROVED: _REQUIRED_EMAIL,
    T.ACCOUNT_REJECTED: _REQUIRED_EMAIL,
    T.ACCOUNT_SUSPENDED: _REQUIRED_EMAIL,
    T.ACCOUNT_REACTIVATED: _REQUIRED_EMAIL,
    T.PAYMENT_CONFIRMED: _OPTIONAL_EMAIL,
    T.PAYMENT_REJECTED: _OPTIONAL_EMAIL,
    T.PAYMENT_REVERSED: _OPTIONAL_EMAIL,
    T.PLOT_ALLOCATED: _OPTIONAL_EMAIL,
    T.PLOT_RELEASED: _OPTIONAL_EMAIL,
}

assert set(EMAIL_POLICY) == set(NotificationType), "Every type needs a policy."
