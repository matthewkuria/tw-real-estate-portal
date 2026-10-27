from rest_framework import serializers

from .models import ServiceProvider, StatusChange
from .services import blacklisted_matches, normalize_kra_pin


class BlacklistedMatch(Exception):
    def __init__(self, matches):
        super().__init__("These details match a blacklisted provider.")
        self.matches = matches


def _name(user):
    if user is None:
        return None

    return user.get_full_name() or user.email


class ServiceProviderSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    estate_names = serializers.SerializerMethodField()
    blacklisted_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    allow_blacklisted_match = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
        help_text=(
            "Set to true to save even though the phone, email or KRA PIN "
            "matches a blacklisted provider."
        ),
    )

    class Meta:
        model = ServiceProvider
        fields = (
            "id",
            "name",
            "category",
            "category_label",
            "description",
            "contact_person",
            "phone",
            "email",
            "address",
            "website",
            "kra_pin",
            "registration_number",
            "estates",
            "estate_names",
            "notes",
            "status",
            "blacklist_reason",
            "blacklisted_at",
            "blacklisted_by_name",
            "created_by_name",
            "created_at",
            "updated_at",
            "allow_blacklisted_match",
        )
        read_only_fields = (
            "id",
            "status",
            "blacklist_reason",
            "blacklisted_at",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "estates": {"required": False},
        }

    def get_estate_names(self, obj) -> list[str]:
        return [estate.name for estate in obj.estates.all()]

    def get_blacklisted_by_name(self, obj) -> str | None:
        return _name(obj.blacklisted_by)

    def get_created_by_name(self, obj) -> str | None:
        return _name(obj.created_by)

    def validate_kra_pin(self, value):
        value = normalize_kra_pin(value)

        if value:
            duplicates = ServiceProvider.objects.filter(kra_pin=value)

            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)

            duplicate = duplicates.first()

            if duplicate is not None:
                # A KRA PIN is one legal entity, so even a blacklisted
                # match can't be added again; it would need reinstating.
                if duplicate.status == ServiceProvider.Status.BLACKLISTED:
                    raise serializers.ValidationError(
                        f"This KRA PIN belongs to blacklisted provider "
                        f"“{duplicate.name}” (id {duplicate.pk}). Reinstate "
                        f"that record instead of adding a new one."
                    )

                raise serializers.ValidationError(
                    f"Provider “{duplicate.name}” (id {duplicate.pk}) already has this KRA PIN."
                )

        return value

    def validate(self, attrs):
        allow_match = attrs.pop("allow_blacklisted_match", False)

        identity = ("phone", "email", "kra_pin")
        changed = self.instance is None or any(
            name in attrs and attrs[name] != getattr(self.instance, name)
            for name in identity
        )

        if changed and not allow_match:
            matches = blacklisted_matches(
                phone=attrs.get("phone", getattr(self.instance, "phone", "")),
                email=attrs.get("email", getattr(self.instance, "email", "")),
                kra_pin=attrs.get("kra_pin", getattr(self.instance, "kra_pin", "")),
                exclude_pk=getattr(self.instance, "pk", None),
            )

            if matches:
                # Not a ValidationError: DRF would turn the ids into
                # strings. The view answers with an E017 envelope.
                raise BlacklistedMatch(matches)

        return attrs


class ResidentServiceProviderSerializer(serializers.ModelSerializer):
    """
    What residents see: how to reach an approved provider. Notes, tax
    and registration numbers, and blacklist details stay with staff.
    """

    category_label = serializers.CharField(source="get_category_display", read_only=True)
    estate_names = serializers.SerializerMethodField()

    class Meta:
        model = ServiceProvider
        fields = (
            "id",
            "name",
            "category",
            "category_label",
            "description",
            "contact_person",
            "phone",
            "email",
            "address",
            "website",
            "estate_names",
        )
        read_only_fields = fields

    def get_estate_names(self, obj) -> list[str]:
        return [estate.name for estate in obj.estates.all()]


class StatusChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StatusChange
        fields = (
            "action",
            "reason",
            "changed_by_name",
            "created_at",
        )
        read_only_fields = fields

    def get_changed_by_name(self, obj) -> str | None:
        return _name(obj.changed_by)


class ServiceProviderDetailSerializer(ServiceProviderSerializer):
    history = StatusChangeSerializer(source="status_changes", many=True, read_only=True)

    class Meta(ServiceProviderSerializer.Meta):
        fields = ServiceProviderSerializer.Meta.fields + ("history",)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=5,
        max_length=2000,
        help_text="Why. Kept in the provider's history.",
    )
