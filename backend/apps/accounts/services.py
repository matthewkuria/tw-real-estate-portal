from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .models import MemberProfile, User


def send_password_reset_email(email):
    """
    Email a password reset link to the frontend if an active user has
    this address. Does nothing otherwise, so callers can't learn which
    emails are registered.
    """
    user = User.objects.filter(email=email, is_active=True).first()

    if user is None or not user.has_usable_password():
        return

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_url = (
        f"{settings.FRONTEND_URL}{settings.PASSWORD_RESET_PATH}"
        f"?uid={uid}&token={token}"
    )

    send_mail(
        subject="Reset your password",
        message=(
            "We received a request to reset your password.\n\n"
            f"Open this link to choose a new one:\n{reset_url}\n\n"
            "If you didn't ask for this, you can ignore this email."
        ),
        from_email=None,
        recipient_list=[user.email],
    )


def revoke_refresh_tokens(user):
    """
    Blacklist every outstanding refresh token for a user, signing them
    out on all devices.
    """
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


@transaction.atomic
def reset_password(user, new_password):
    """
    Set a new password and sign the user out everywhere.
    """
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])

    revoke_refresh_tokens(user)

    return user


@transaction.atomic
def create_member_profile(user):
    """
    Create a MemberProfile for a user if one does not already exist.
    """
    profile, _ = MemberProfile.objects.get_or_create(user=user)
    return profile


@transaction.atomic
def approve_member(user):
    """
    Approve a pending member and activate their account.
    """
    if user.status != User.Status.PENDING:
        raise ValueError(
            f"Cannot approve a member with status '{user.status}'."
        )

    user.status = User.Status.ACTIVE
    user.is_active = True
    user.approved_at = timezone.now()

    user.save(
        update_fields=[
            "status",
            "is_active",
            "approved_at",
            "updated_at",
        ]
    )

    create_member_profile(user)

    return user


@transaction.atomic
def reject_member(user):
    """
    Reject a pending member and deactivate their account.
    """
    if user.status != User.Status.PENDING:
        raise ValueError(
            f"Cannot reject a member with status '{user.status}'."
        )

    user.status = User.Status.REJECTED
    user.is_active = False

    user.save(
        update_fields=[
            "status",
            "is_active",
            "updated_at",
        ]
    )

    return user


@transaction.atomic
def suspend_member(user):
    """
    Suspend an active member and deactivate their account.
    """
    if user.status != User.Status.ACTIVE:
        raise ValueError(
            f"Cannot suspend a member with status '{user.status}'."
        )

    user.status = User.Status.SUSPENDED
    user.is_active = False

    user.save(
        update_fields=[
            "status",
            "is_active",
            "updated_at",
        ]
    )

    return user


@transaction.atomic
def reactivate_member(user):
    """
    Reactivate a suspended member.
    """
    if user.status != User.Status.SUSPENDED:
        raise ValueError(
            f"Cannot reactivate a member with status '{user.status}'."
        )

    user.status = User.Status.ACTIVE
    user.is_active = True

    user.save(
        update_fields=[
            "status",
            "is_active",
            "updated_at",
        ]
    )

    return user