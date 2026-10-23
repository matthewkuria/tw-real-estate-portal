
from rest_framework.permissions import BasePermission, SAFE_METHODS


STAFF_PAYMENT_ROLES = {
    "SUPER_ADMIN",
    "ADMIN",
    "Manager",
    "FINANCE_OFFICER",
}


class PaymentPermission(BasePermission):
    """
    Staff can manage payments.
    Members can read only their own payments.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.role in STAFF_PAYMENT_ROLES:
            return True

        if user.role == "MEMBER" and request.method in SAFE_METHODS:
            return True

        return False

    def has_object_permission(self, request, view, obj):
        user = request.user

        if user.role in STAFF_PAYMENT_ROLES:
            return True

        return (
            user.role == "MEMBER"
            and request.method in SAFE_METHODS
            and obj.member_id == user.id
        )
