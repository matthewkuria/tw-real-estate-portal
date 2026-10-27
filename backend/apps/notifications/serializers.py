from rest_framework import serializers

from .models import Notification
from .types import EMAIL_POLICY, NotificationType


class ActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()

    def get_name(self, obj) -> str:
        return obj.get_full_name() or "Member"


class NotificationSerializer(serializers.ModelSerializer):
    actor = ActorSerializer(read_only=True, allow_null=True)
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "type",
            "title",
            "body",
            "link",
            "actor",
            "data",
            "is_read",
            "read_at",
            "created_at",
        )
        read_only_fields = fields


class PreferenceSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=NotificationType.choices)
    label = serializers.CharField(read_only=True)
    sends_email = serializers.BooleanField(read_only=True)
    can_opt_out = serializers.BooleanField(read_only=True)
    email_enabled = serializers.BooleanField()


class PreferenceUpdateItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=NotificationType.choices)
    email_enabled = serializers.BooleanField()

    def validate(self, attrs):
        policy = EMAIL_POLICY[attrs["type"]]

        if not policy.sends_email:
            raise serializers.ValidationError(
                {"type": "This notification type is never emailed."}
            )

        if not policy.can_opt_out:
            raise serializers.ValidationError(
                {"type": "Emails for this notification type can't be turned off."}
            )

        return attrs


class PreferenceUpdateSerializer(serializers.Serializer):
    preferences = PreferenceUpdateItemSerializer(many=True)


class MarkedSerializer(serializers.Serializer):
    marked = serializers.IntegerField()
    unread_count = serializers.IntegerField()


class UnreadCountSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField()
