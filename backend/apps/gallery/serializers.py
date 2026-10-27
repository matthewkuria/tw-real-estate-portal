from rest_framework import serializers

from .models import GalleryImage
from .processing import InvalidImage, process_upload


class FormBooleanField(serializers.BooleanField):
    """
    DRF reads a boolean missing from form data as False (HTML checkbox
    rules), so a multipart upload that leaves it out would silently set
    it to False. Treat "missing" as "not given" so the default applies.
    """

    default_empty_html = serializers.empty


class GalleryImageSerializer(serializers.ModelSerializer):
    """
    `image` is a file on upload (multipart/form-data) and an absolute URL
    in responses.
    """

    alt_text = serializers.CharField(max_length=250, required=False, allow_blank=True)
    is_published = FormBooleanField(default=True)
    estate_name = serializers.SerializerMethodField()

    class Meta:
        model = GalleryImage
        fields = (
            "id",
            "title",
            "caption",
            "alt_text",
            "image",
            "thumbnail",
            "width",
            "height",
            "estate",
            "estate_name",
            "is_published",
            "display_order",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "thumbnail",
            "width",
            "height",
            "created_at",
            "updated_at",
        )

    def get_estate_name(self, obj) -> str | None:
        return obj.estate.name if obj.estate_id else None

    def validate_image(self, upload):
        try:
            processed = process_upload(upload)
        except InvalidImage as exc:
            raise serializers.ValidationError(str(exc))

        # Saved alongside the image in create()/update().
        self._thumbnail = processed.thumbnail
        return processed.image

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["alt_text"] = instance.alt_text or instance.title
        return data

    def create(self, validated_data):
        return super().create(
            {**validated_data, "thumbnail": self._thumbnail},
        )

    def update(self, instance, validated_data):
        if "image" in validated_data:
            validated_data["thumbnail"] = self._thumbnail

        return super().update(instance, validated_data)


class ReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text="Image ids in the order they should be shown.",
    )

    def validate_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each id may appear only once.")

        found = set(GalleryImage.objects.filter(pk__in=ids).values_list("pk", flat=True))
        missing = [i for i in ids if i not in found]

        if missing:
            raise serializers.ValidationError(f"Unknown image ids: {missing}")

        return ids
