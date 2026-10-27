from django.utils import timezone
from rest_framework import serializers

from apps.common.fields import FormBooleanField
from apps.gallery.processing import InvalidImage, process_upload

from .models import Advertisement


def ad_state(ad, now=None):
    """
    Where an ad is in its life: draft, scheduled, live or expired.
    """
    now = now or timezone.now()

    if not ad.is_published:
        return "DRAFT"

    if ad.publish_at > now:
        return "SCHEDULED"

    if ad.expires_at is not None and ad.expires_at <= now:
        return "EXPIRED"

    return "LIVE"


class AdvertisementSerializer(serializers.ModelSerializer):
    """
    Send multipart/form-data to include an `image`; JSON works for
    everything else. `image` and `thumbnail` come back as URLs.
    """

    category_label = serializers.CharField(source="get_category_display", read_only=True)
    estate_name = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField()

    is_published = FormBooleanField(default=True)
    is_featured = FormBooleanField(default=False)

    remove_image = FormBooleanField(
        write_only=True,
        required=False,
        help_text="Set to true to remove the current image.",
    )

    class Meta:
        model = Advertisement
        fields = (
            "id",
            "category",
            "category_label",
            "title",
            "summary",
            "description",
            "image",
            "thumbnail",
            "image_width",
            "image_height",
            "remove_image",
            "price",
            "event_starts_at",
            "event_ends_at",
            "location",
            "link_url",
            "link_label",
            "contact_name",
            "contact_phone",
            "contact_email",
            "visibility",
            "estate",
            "estate_name",
            "is_published",
            "is_featured",
            "publish_at",
            "expires_at",
            "state",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "thumbnail",
            "image_width",
            "image_height",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {
            "price": {"min_value": 0},
        }

    def get_estate_name(self, obj) -> str | None:
        return obj.estate.name if obj.estate_id else None

    def get_state(self, obj) -> str:
        return ad_state(obj)

    def validate_image(self, upload):
        if upload is None:
            return upload

        try:
            processed = process_upload(upload)
        except InvalidImage as exc:
            raise serializers.ValidationError(str(exc))

        self._thumbnail = processed.thumbnail
        return processed.image

    def _value(self, attrs, name):
        if name in attrs:
            return attrs[name]

        return getattr(self.instance, name, None)

    def validate(self, attrs):
        category = self._value(attrs, "category")
        starts = self._value(attrs, "event_starts_at")
        ends = self._value(attrs, "event_ends_at")
        publish_at = self._value(attrs, "publish_at") or timezone.now()
        expires_at = self._value(attrs, "expires_at")

        if category == Advertisement.Category.EVENT:
            if starts is None:
                raise serializers.ValidationError(
                    {"event_starts_at": "An event needs a start time."}
                )

            if ends is not None and ends < starts:
                raise serializers.ValidationError(
                    {"event_ends_at": "The event can't end before it starts."}
                )

            # Unless told otherwise, stop showing the ad once the event is
            # over.
            if "expires_at" not in attrs and (self.instance is None or self.instance.expires_at is None):
                attrs["expires_at"] = expires_at = ends or starts

        if expires_at is not None and expires_at <= publish_at:
            raise serializers.ValidationError(
                {"expires_at": "The ad must expire after it's published."}
            )

        if self._value(attrs, "link_label") and not self._value(attrs, "link_url"):
            raise serializers.ValidationError(
                {"link_url": "Add the link for this button."}
            )

        return attrs

    def create(self, validated_data):
        validated_data.pop("remove_image", None)

        if validated_data.get("image"):
            validated_data["thumbnail"] = self._thumbnail

        return super().create(validated_data)

    def update(self, instance, validated_data):
        remove_image = validated_data.pop("remove_image", False)

        if validated_data.get("image"):
            validated_data["thumbnail"] = self._thumbnail
        elif remove_image:
            # Empty, not None: file columns aren't nullable.
            validated_data["image"] = ""
            validated_data["thumbnail"] = ""

        return super().update(instance, validated_data)
