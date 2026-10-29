from rest_framework import serializers

from apps.common.fields import FormBooleanField
from apps.gallery.processing import InvalidImage, process_upload

from .models import ListingPhoto, PropertyListing


MAX_PHOTOS = 20


def clean_image(upload):
    """
    Re-encode an upload; returns the (image, thumbnail) files to store.
    """
    try:
        processed = process_upload(upload)
    except InvalidImage as exc:
        raise serializers.ValidationError(str(exc))

    return processed.image, processed.thumbnail


def save_photos(listing, images):
    """
    Store cleaned (image, thumbnail) pairs after the listing's current photos.
    """
    last = listing.photos.order_by("-position").values_list("position", flat=True).first()
    start = -1 if last is None else last

    for offset, (image, thumbnail) in enumerate(images, start=1):
        ListingPhoto.objects.create(
            listing=listing,
            image=image,
            thumbnail=thumbnail,
            position=start + offset,
        )


class ListingPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListingPhoto
        fields = (
            "id",
            "image",
            "thumbnail",
            "width",
            "height",
            "caption",
            "position",
            "created_at",
        )
        read_only_fields = (
            "id",
            "image",
            "thumbnail",
            "width",
            "height",
            "created_at",
        )


class PhotoUploadSerializer(serializers.Serializer):
    images = serializers.ListField(
        child=serializers.ImageField(),
        min_length=1,
        max_length=MAX_PHOTOS,
        help_text="One or more JPEG, PNG or WebP files.",
    )

    def validate_images(self, uploads):
        return [clean_image(upload) for upload in uploads]


class PropertyListingSerializer(serializers.ModelSerializer):
    """
    Send multipart/form-data to include `images` on create; JSON works
    for everything else. Repeat `features` (and `images`) once per value.
    """

    listing_type_label = serializers.CharField(source="get_listing_type_display", read_only=True)
    property_type_label = serializers.CharField(source="get_property_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    estate_name = serializers.SerializerMethodField()
    plot_number = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()
    photo_count = serializers.SerializerMethodField()

    features = serializers.ListField(
        child=serializers.CharField(max_length=60),
        max_length=30,
        required=False,
    )

    is_published = FormBooleanField(default=True)
    is_featured = FormBooleanField(default=False)

    images = serializers.ListField(
        child=serializers.ImageField(),
        max_length=MAX_PHOTOS,
        required=False,
        write_only=True,
        help_text="Create only: photos to attach, in display order.",
    )

    class Meta:
        model = PropertyListing
        fields = (
            "id",
            "title",
            "description",
            "listing_type",
            "listing_type_label",
            "property_type",
            "property_type_label",
            "status",
            "status_label",
            "price",
            "price_period",
            "location",
            "county",
            "estate",
            "estate_name",
            "plot",
            "plot_number",
            "bedrooms",
            "bathrooms",
            "floor_area_sqm",
            "land_size_acres",
            "features",
            "contact_name",
            "contact_phone",
            "contact_email",
            "is_published",
            "is_featured",
            "cover_photo",
            "photo_count",
            "images",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "price": {"min_value": 1},
            "floor_area_sqm": {"min_value": 0},
            "land_size_acres": {"min_value": 0},
        }

    def get_estate_name(self, obj) -> str | None:
        return obj.estate.name if obj.estate_id else None

    def get_plot_number(self, obj) -> str | None:
        return obj.plot.plot_number if obj.plot_id else None

    def get_cover_photo(self, obj) -> str | None:
        photos = obj.photos.all()

        if not photos:
            return None

        photo = photos[0]
        field = photo.thumbnail or photo.image
        request = self.context.get("request")

        return request.build_absolute_uri(field.url) if request else field.url

    def get_photo_count(self, obj) -> int:
        return len(obj.photos.all())

    def validate_features(self, features):
        cleaned = []

        for feature in features:
            feature = feature.strip()

            if feature and feature.lower() not in {f.lower() for f in cleaned}:
                cleaned.append(feature)

        return cleaned

    def validate_images(self, uploads):
        if self.instance is not None:
            raise serializers.ValidationError("Use the photos endpoint to add photos to a listing.")

        return [clean_image(upload) for upload in uploads]

    def _value(self, attrs, name):
        if name in attrs:
            return attrs[name]

        return getattr(self.instance, name, None)

    def validate(self, attrs):
        errors = {}

        listing_type = self._value(attrs, "listing_type")
        period = self._value(attrs, "price_period")

        if listing_type == PropertyListing.ListingType.RENT and not period:
            errors["price_period"] = "Say whether the rent is per month or per year."
        elif listing_type == PropertyListing.ListingType.SALE and period:
            errors["price_period"] = "Only rentals have a price period."

        status = self._value(attrs, "status")

        if listing_type == PropertyListing.ListingType.SALE and status == PropertyListing.Status.RENTED:
            errors["status"] = "A listing for sale can't be marked as rented."
        elif listing_type == PropertyListing.ListingType.RENT and status == PropertyListing.Status.SOLD:
            errors["status"] = "A listing for rent can't be marked as sold."

        plot = self._value(attrs, "plot")
        estate = self._value(attrs, "estate")

        if plot is not None:
            if "estate" in attrs:
                if estate is None or plot.estate_id != estate.pk:
                    errors["plot"] = "This plot isn't in the chosen estate."
            elif "plot" in attrs:
                # The plot says which estate the listing is in.
                attrs["estate"] = plot.estate

        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    def create(self, validated_data):
        images = validated_data.pop("images", [])
        listing = super().create(validated_data)
        save_photos(listing, images)

        return listing


class PropertyListingDetailSerializer(PropertyListingSerializer):
    photos = ListingPhotoSerializer(many=True, read_only=True)

    class Meta(PropertyListingSerializer.Meta):
        fields = PropertyListingSerializer.Meta.fields + ("photos",)
