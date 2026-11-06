from rest_framework.permissions import BasePermission

from apps.accounts.models import User


# Record payments for any member and see every payment.
RECORDER_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.MANAGER,
    User.Role.FINANCE_OFFICER,
}

# Confirm, reject and reverse payments.
APPROVER_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.FINANCE_OFFICER,
}


def is_recorder(user):
    return user.is_authenticated and user.role in RECORDER_ROLES


def is_approver(user):
    return user.is_authenticated and user.role in APPROVER_ROLES


class CanUsePayments(BasePermission):
    """
    Staff who record payments, and members, who see and submit their own.
    """

    message = "You don't have access to payments."

    def has_permission(self, request, view):
        user = request.user

        return user.is_authenticated and (
            user.role in RECORDER_ROLES or user.role == User.Role.MEMBER
        )


class CanEditPayments(BasePermission):
    message = "Only staff can edit payments."

    def has_permission(self, request, view):
        return is_recorder(request.user)


class CanApprovePayments(BasePermission):
    message = "Only Super Admins, Admins and Finance Officers can confirm, reject or reverse payments."

    def has_permission(self, request, view):
        return is_approver(request.user)
