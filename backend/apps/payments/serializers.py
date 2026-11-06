from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.common.fields import FormBooleanField
from apps.properties.models import Property
from apps.rentals.serializers import PersonSerializer

from .models import Payment
from .permissions import is_approver, is_recorder


# Methods whose references come from a bank or M-Pesa and so can't repeat.
UNIQUE_REFERENCE_METHODS = {
    Payment.Method.MPESA,
    Payment.Method.BANK_TRANSFER,
    Payment.Method.CHEQUE,
}


class PaymentSerializer(serializers.ModelSerializer):
    """
    Staff pick the member; a member submitting a payment is always the
    member, and it always starts pending. Status changes go through the
    confirm, reject and reverse actions.
    """

    member = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.MEMBER),
        required=False,
        help_text="Staff only; members always pay for themselves.",
    )
    member_detail = PersonSerializer(source="member", read_only=True)
    estate_name = serializers.CharField(source="estate.name", read_only=True)
    plot_number = serializers.CharField(source="property.plot_number", read_only=True, allow_null=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    recorded_by = PersonSerializer(read_only=True)
    confirmed_by = PersonSerializer(read_only=True, allow_null=True)
    status_changed_by = PersonSerializer(read_only=True, allow_null=True)

    confirm = FormBooleanField(
        write_only=True,
        default=False,
        help_text=(
            "Record the payment as already confirmed, e.g. cash taken at the "
            "office. Super Admins, Admins and Finance Officers only."
        ),
    )

    class Meta:
        model = Payment
        fields = (
            "id",
            "member",
            "member_detail",
            "estate",
            "estate_name",
            "property",
            "plot_number",
            "amount",
            "method",
            "method_label",
            "reference",
            "status",
            "status_label",
            "received_at",
            "notes",
            "recorded_by",
            "confirmed_by",
            "confirmed_at",
            "status_changed_by",
            "status_changed_at",
            "status_reason",
            "confirm",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "confirmed_at",
            "status_changed_at",
            "status_reason",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "amount": {"min_value": Decimal("0.01")},
            "property": {"help_text": "The member's plot in this estate, if the payment is for one."},
        }

    def validate_received_at(self, value):
        if value > timezone.now():
            raise serializers.ValidationError("Can't be in the future.")

        return value

    def validate_reference(self, value):
        return value.strip()

    def validate(self, attrs):
        user = self.context["request"].user
        instance = self.instance

        if instance is not None:
            attrs.pop("confirm", None)

            if "member" in attrs and attrs["member"] != instance.member:
                raise serializers.ValidationError({"member": "A payment can't be moved to another member."})
        elif is_recorder(user):
            if "member" not in attrs:
                raise serializers.ValidationError({"member": "This field is required."})

            if attrs.get("confirm") and not is_approver(user):
                raise serializers.ValidationError(
                    {"confirm": "Only Super Admins, Admins and Finance Officers can confirm payments."}
                )
        else:
            attrs["member"] = user

            if attrs.get("confirm"):
                raise serializers.ValidationError({"confirm": "Members can't confirm their own payments."})

        def current(name):
            return attrs.get(name, getattr(instance, name, None))

        member = current("member")
        estate = current("estate")
        plot = current("property")
        method = current("method")
        reference = current("reference") or ""

        if estate is not None and not estate.is_active and (instance is None or "estate" in attrs):
            raise serializers.ValidationError({"estate": "This estate isn't active."})

        if plot is not None:
            if plot.estate_id != estate.pk:
                raise serializers.ValidationError({"property": "The plot must be in the selected estate."})

            if plot.status != Property.Status.ALLOCATED or plot.allocated_to_id != member.pk:
                raise serializers.ValidationError({"property": "This plot isn't allocated to the member."})

        if method in UNIQUE_REFERENCE_METHODS:
            if not reference:
                raise serializers.ValidationError(
                    {"reference": "Give the transaction reference for this payment method."}
                )

            clash = Payment.objects.filter(reference__iexact=reference).exclude(
                status=Payment.Status.REJECTED
            )

            if instance is not None:
                clash = clash.exclude(pk=instance.pk)

            if clash.exists():
                raise serializers.ValidationError(
                    {"reference": "A payment with this reference has already been recorded."}
                )

        return attrs

    def create(self, validated_data):
        user = self.context["request"].user
        confirm = validated_data.pop("confirm", False)

        validated_data["recorded_by"] = user

        if confirm:
            validated_data["status"] = Payment.Status.CONFIRMED
            validated_data["confirmed_by"] = user
            validated_data["confirmed_at"] = timezone.now()

        return super().create(validated_data)


class PaymentReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(help_text="Why, e.g. reference not found on statement, cheque bounced.")
