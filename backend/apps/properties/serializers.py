from rest_framework import serializers

from apps.accounts.models import User
from apps.common.fields import FormBooleanField
from apps.rentals.serializers import PersonSerializer

from .models import PlotAllocation, Property


class PlotSerializer(serializers.ModelSerializer):
    """
    Allocation fields are read-only here; use the allocate and release
    actions to change who holds a plot.
    """

    estate_name = serializers.CharField(source="estate.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    member = PersonSerializer(source="allocated_to", read_only=True, allow_null=True)
    amount_paid = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = (
            "id",
            "estate",
            "estate_name",
            "plot_number",
            "description",
            "price",
            "required_deposit",
            "status",
            "status_label",
            "member",
            "allocated_at",
            "amount_paid",
            "balance",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "allocated_at",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "price": {"min_value": 1},
            "required_deposit": {"min_value": 1},
        }
        # Plot numbers are checked in validate(), ignoring case.
        validators = []

    def get_amount_paid(self, obj) -> str | None:
        """
        Confirmed payments by the current holder toward this plot.
        """
        if obj.allocated_to_id is None:
            return None

        return str(obj.amount_paid)

    def get_balance(self, obj) -> str | None:
        if obj.allocated_to_id is None:
            return None

        return str(max(obj.price - obj.amount_paid, 0))

    def validate(self, attrs):
        estate = attrs.get("estate", getattr(self.instance, "estate", None))
        number = attrs.get("plot_number", getattr(self.instance, "plot_number", None))
        price = attrs.get("price", getattr(self.instance, "price", None))
        deposit = attrs.get("required_deposit", getattr(self.instance, "required_deposit", None))

        if self.instance is not None and "estate" in attrs and attrs["estate"] != self.instance.estate:
            raise serializers.ValidationError(
                {"estate": "A plot can't be moved to another estate."}
            )

        if "plot_number" in attrs:
            number = attrs["plot_number"] = attrs["plot_number"].strip()

        if estate is not None and number:
            clash = Property.objects.filter(estate=estate, plot_number__iexact=number)

            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)

            if clash.exists():
                raise serializers.ValidationError(
                    {"plot_number": "This estate already has a plot with that number."}
                )

        if price is not None and deposit is not None and deposit > price:
            raise serializers.ValidationError(
                {"required_deposit": "The deposit can't be more than the price."}
            )

        return attrs


class AllocateSerializer(serializers.Serializer):
    member = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    waive_deposit = FormBooleanField(
        default=False,
        help_text="Allocate before the full deposit is paid. Needs `notes` saying why.",
    )


class ReleaseSerializer(serializers.Serializer):
    reason = serializers.CharField(help_text="Why the plot is being taken back, e.g. member withdrew.")


class PlotAllocationSerializer(serializers.ModelSerializer):
    member = PersonSerializer(read_only=True)
    allocated_by = PersonSerializer(read_only=True, allow_null=True)
    released_by = PersonSerializer(read_only=True, allow_null=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = PlotAllocation
        fields = (
            "id",
            "member",
            "allocated_at",
            "allocated_by",
            "deposit_paid",
            "deposit_waived",
            "notes",
            "released_at",
            "released_by",
            "release_reason",
            "is_current",
        )
        read_only_fields = fields

    def get_is_current(self, obj) -> bool:
        return obj.released_at is None
