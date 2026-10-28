from rest_framework import serializers

from apps.accounts.models import User
from apps.gallery.processing import InvalidImage, process_upload

from .models import HousePhoto, RentalHouse, Tenant


class PersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()
    email = serializers.EmailField()

    def get_name(self, obj) -> str:
        return obj.get_full_name() or obj.email


class HousePhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = HousePhoto
        fields = (
            "id",
            "image",
            "thumbnail",
            "width",
            "height",
            "caption",
            "created_at",
        )
        read_only_fields = (
            "id",
            "thumbnail",
            "width",
            "height",
            "created_at",
        )

    def validate_image(self, upload):
        try:
            processed = process_upload(upload)
        except InvalidImage as exc:
            raise serializers.ValidationError(str(exc))

        self._thumbnail = processed.thumbnail
        return processed.image

    def create(self, validated_data):
        return super().create({**validated_data, "thumbnail": self._thumbnail})


class TenantSerializer(serializers.ModelSerializer):
    house_number = serializers.CharField(source="house.house_number", read_only=True)
    plot_number = serializers.CharField(source="house.property.plot_number", read_only=True)
    estate_name = serializers.CharField(source="house.property.estate.name", read_only=True)

    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="Optional: the tenant's portal account.",
    )
    account = PersonSerializer(source="user", read_only=True, allow_null=True)

    class Meta:
        model = Tenant
        fields = (
            "id",
            "house",
            "house_number",
            "plot_number",
            "estate_name",
            "full_name",
            "phone",
            "email",
            "id_number",
            "move_in_date",
            "notes",
            "user",
            "account",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )

    def validate_user(self, user):
        if user is None:
            return user

        linked = Tenant.objects.filter(user=user)

        if self.instance is not None:
            linked = linked.exclude(pk=self.instance.pk)

        if linked.exists():
            raise serializers.ValidationError(
                "This account is already linked to another tenant."
            )

        return user


class HouseTenantSerializer(serializers.ModelSerializer):
    """
    A tenant as listed inside their house.
    """

    has_account = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = (
            "id",
            "full_name",
            "phone",
            "email",
            "move_in_date",
            "has_account",
        )
        read_only_fields = fields

    def get_has_account(self, obj) -> bool:
        return obj.user_id is not None


class RentalHouseSerializer(serializers.ModelSerializer):
    house_type_label = serializers.CharField(source="get_house_type_display", read_only=True)
    plot_number = serializers.CharField(source="property.plot_number", read_only=True)
    estate = serializers.IntegerField(source="property.estate_id", read_only=True)
    estate_name = serializers.CharField(source="property.estate.name", read_only=True)
    landlord = PersonSerializer(source="property.allocated_to", read_only=True, allow_null=True)
    tenant_count = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()

    class Meta:
        model = RentalHouse
        fields = (
            "id",
            "property",
            "plot_number",
            "estate",
            "estate_name",
            "landlord",
            "house_number",
            "house_type",
            "house_type_label",
            "bedrooms",
            "description",
            "tenant_count",
            "cover_photo",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )

    def get_tenant_count(self, obj) -> int:
        return len(obj.tenants.all())

    def get_cover_photo(self, obj) -> str | None:
        photos = obj.photos.all()

        if not photos:
            return None

        photo = photos[0]
        field = photo.thumbnail or photo.image
        request = self.context.get("request")

        return request.build_absolute_uri(field.url) if request else field.url

    def validate(self, attrs):
        prop = attrs.get("property", getattr(self.instance, "property", None))
        number = attrs.get("house_number", getattr(self.instance, "house_number", None))

        if prop is not None and number:
            clash = RentalHouse.objects.filter(property=prop, house_number__iexact=number.strip())

            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)

            if clash.exists():
                raise serializers.ValidationError(
                    {"house_number": "This plot already has a house with that number."}
                )

        if "house_number" in attrs:
            attrs["house_number"] = attrs["house_number"].strip()

        return attrs


class RentalHouseDetailSerializer(RentalHouseSerializer):
    tenants = HouseTenantSerializer(many=True, read_only=True)
    photos = HousePhotoSerializer(many=True, read_only=True)

    class Meta(RentalHouseSerializer.Meta):
        fields = RentalHouseSerializer.Meta.fields + ("tenants", "photos")
