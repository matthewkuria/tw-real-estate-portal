from django.db import transaction
from django.utils import timezone

from .models import MemberProfile, User


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