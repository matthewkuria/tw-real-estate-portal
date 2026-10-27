from django.utils import timezone
from rest_framework import serializers

from .content import has_text, sanitize
from .models import Newsletter


class NewsletterSerializer(serializers.ModelSerializer):
    estate_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Newsletter
        fields = (
            "id",
            "subject",
            "preheader",
            "body_html",
            "audience",
            "estate",
            "estate_name",
            "status",
            "scheduled_for",
            "sent_at",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "scheduled_for",
            "sent_at",
            "created_at",
            "updated_at",
        )

    def get_estate_name(self, obj) -> str | None:
        return obj.estate.name if obj.estate_id else None

    def get_created_by_name(self, obj) -> str | None:
        if obj.created_by is None:
            return None

        return obj.created_by.get_full_name() or obj.created_by.email

    def validate_body_html(self, value):
        cleaned = sanitize(value)

        if not has_text(cleaned):
            raise serializers.ValidationError("The newsletter has no content.")

        return cleaned

    def validate(self, attrs):
        audience = attrs.get("audience", getattr(self.instance, "audience", None))
        estate = attrs.get("estate", getattr(self.instance, "estate", None))

        if audience == Newsletter.Audience.ESTATE_MEMBERS and estate is None:
            raise serializers.ValidationError(
                {"estate": "Choose the estate this newsletter is for."}
            )

        if audience != Newsletter.Audience.ESTATE_MEMBERS:
            attrs["estate"] = None

        return attrs


class StatsSerializer(serializers.Serializer):
    recipients = serializers.IntegerField()
    sent = serializers.IntegerField()
    pending = serializers.IntegerField()
    failed = serializers.IntegerField()


class NewsletterDetailSerializer(NewsletterSerializer):
    """
    Adds delivery numbers for admins once a newsletter has gone out.
    """

    stats = serializers.SerializerMethodField()

    class Meta(NewsletterSerializer.Meta):
        fields = NewsletterSerializer.Meta.fields + ("stats",)

    def get_stats(self, obj) -> StatsSerializer(allow_null=True):
        from .services import delivery_stats

        if obj.status != Newsletter.Status.SENT:
            return None

        return delivery_stats(obj)


class ArchiveSerializer(serializers.ModelSerializer):
    """
    What members see when reading past issues in the portal.
    """

    class Meta:
        model = Newsletter
        fields = (
            "id",
            "subject",
            "preheader",
            "body_html",
            "sent_at",
        )
        read_only_fields = fields


class PreviewSerializer(serializers.Serializer):
    html = serializers.CharField()
    text = serializers.CharField()
    recipient_count = serializers.IntegerField()


class TestSendSerializer(serializers.Serializer):
    emails = serializers.ListField(
        child=serializers.EmailField(),
        required=False,
        max_length=5,
        help_text="Up to 5 addresses. Defaults to your own email.",
    )


class ScheduleSerializer(serializers.Serializer):
    send_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Leave out to send now.",
    )

    def validate_send_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Choose a time in the future.")

        return value


class SubscriptionSerializer(serializers.Serializer):
    subscribed = serializers.BooleanField()


class TokenStatusSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="Partly hidden, e.g. ja***@example.com.")
    subscribed = serializers.BooleanField()
