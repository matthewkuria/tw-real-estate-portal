from rest_framework.permissions import BasePermission

from .models import User


class IsSuperAdmin(BasePermission):
    message = "Only Super Admins can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.SUPER_ADMIN
        )


class IsAdmin(BasePermission):
    message = "Only Admins can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.ADMIN
        )


class IsManager(BasePermission):
    message = "Only Managers can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.MANAGER
        )


class IsFinanceOfficer(BasePermission):
    message = "Only Finance Officers can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.FINANCE_OFFICER
        )


class IsMember(BasePermission):
    message = "Only Members can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.MEMBER
        )


class IsStaffRole(BasePermission):
    message = "Staff access is required."

    STAFF_ROLES = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.FINANCE_OFFICER,
    }

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in self.STAFF_ROLES
        )


class IsAdminOrManager(BasePermission):
    message = "Admin or Manager access is required."

    ALLOWED_ROLES = {
        User.Role.ADMIN,
        User.Role.MANAGER,
    }

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in self.ALLOWED_ROLES
        )


class IsSuperAdminOrAdmin(BasePermission):
    message = "Super Admin or Admin access is required."

    ALLOWED_ROLES = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
    }

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in self.ALLOWED_ROLES
        )