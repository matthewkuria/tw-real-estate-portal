"""
Who can see and manage community content.

Content with no estate is visible to everyone. Content tagged with an
estate is visible to staff and to members holding a plot in that estate.
"""

from django.db.models import Q
from rest_framework.permissions import BasePermission

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole
from apps.properties.models import Property


UPDATE_AUTHOR_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}

MODERATOR_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.MANAGER,
}


def sees_all_estates(user):
    return user.role in IsStaffRole.STAFF_ROLES


def is_moderator(user):
    return user.role in MODERATOR_ROLES


def member_estate_ids(user):
    return set(
        Property.objects
        .filter(allocated_to=user)
        .values_list("estate_id", flat=True)
        .distinct()
    )


def visible_to(queryset, user):
    if sees_all_estates(user):
        return queryset

    return queryset.filter(
        Q(estate__isnull=True) | Q(estate_id__in=member_estate_ids(user))
    )


def can_view_estate(user, estate_id):
    return (
        estate_id is None
        or sees_all_estates(user)
        or estate_id in member_estate_ids(user)
    )


class CanPostUpdates(BasePermission):
    message = "Only Super Admins and Admins can manage updates."

    def has_permission(self, request, view):
        return request.user.role in UPDATE_AUTHOR_ROLES


class IsModerator(BasePermission):
    message = "Only moderators can perform this action."

    def has_permission(self, request, view):
        return is_moderator(request.user)


class IsAuthorOrModerator(BasePermission):
    message = "You can only change your own posts."

    def has_object_permission(self, request, view, obj):
        return obj.author_id == request.user.pk or is_moderator(request.user)
