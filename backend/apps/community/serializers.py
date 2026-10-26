from collections import Counter

from rest_framework import serializers

from apps.accounts.models import User

from .access import can_view_estate
from .models import Message, Reaction, Thread, Update


class AuthorSerializer(serializers.ModelSerializer):
    """
    Public view of a poster. Email is deliberately left out.
    """

    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "role",
        )

    def get_name(self, obj) -> str:
        return obj.get_full_name() or "Member"


class EstateNameMixin(serializers.Serializer):
    estate_name = serializers.SerializerMethodField()

    def get_estate_name(self, obj) -> str | None:
        return obj.estate.name if obj.estate_id else None


# ============================================================================
# UPDATES
# ============================================================================

class UpdateSerializer(EstateNameMixin, serializers.ModelSerializer):
    author = AuthorSerializer(read_only=True)

    class Meta:
        model = Update
        fields = (
            "id",
            "title",
            "body",
            "estate",
            "estate_name",
            "author",
            "is_pinned",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "author",
            "created_at",
            "updated_at",
        )


# ============================================================================
# FORUM
# ============================================================================

class ThreadSerializer(EstateNameMixin, serializers.ModelSerializer):
    author = AuthorSerializer(read_only=True)
    message_count = serializers.SerializerMethodField()

    # The opening message, only used when creating a thread.
    body = serializers.CharField(
        write_only=True,
        max_length=4000,
        required=False,
    )

    class Meta:
        model = Thread
        fields = (
            "id",
            "title",
            "body",
            "estate",
            "estate_name",
            "author",
            "is_pinned",
            "is_locked",
            "message_count",
            "last_message_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "author",
            "is_pinned",
            "is_locked",
            "last_message_at",
            "created_at",
        )

    def get_message_count(self, obj) -> int:
        count = getattr(obj, "message_count", None)

        if count is None:
            count = obj.messages.filter(is_deleted=False).count()

        return count

    def validate_estate(self, estate):
        if self.instance is not None:
            if estate != self.instance.estate:
                raise serializers.ValidationError(
                    "A thread's estate cannot be changed."
                )
            return estate

        user = self.context["request"].user
        estate_id = estate.pk if estate else None

        if not can_view_estate(user, estate_id):
            raise serializers.ValidationError(
                "You can only post in estates where you hold a plot."
            )

        return estate

    def validate(self, attrs):
        if self.instance is None and not attrs.get("body", "").strip():
            raise serializers.ValidationError(
                {"body": "A new thread needs an opening message."}
            )

        return attrs


class MessageSerializer(serializers.ModelSerializer):
    author = AuthorSerializer(read_only=True)
    reactions = serializers.SerializerMethodField()
    my_reactions = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = (
            "id",
            "thread",
            "author",
            "body",
            "reply_to",
            "edited_at",
            "is_deleted",
            "reactions",
            "my_reactions",
            "created_at",
        )
        read_only_fields = (
            "id",
            "thread",
            "author",
            "edited_at",
            "is_deleted",
            "created_at",
        )

    def get_reactions(self, obj) -> dict[str, int]:
        """
        Counts per emoji, e.g. {"like": 3, "love": 1}.
        """
        return dict(Counter(r.emoji for r in obj.reactions.all()))

    def get_my_reactions(self, obj) -> list[str]:
        request = self.context.get("request")

        if request is None:
            return []

        return [
            r.emoji for r in obj.reactions.all()
            if r.user_id == request.user.pk
        ]

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message cannot be empty.")

        return value

    def validate_reply_to(self, reply_to):
        thread = self.context["thread"]

        if reply_to is not None and reply_to.thread_id != thread.pk:
            raise serializers.ValidationError(
                "You can only reply to a message in the same thread."
            )

        return reply_to

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if instance.is_deleted:
            data["body"] = ""

        return data


class MessageEditSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4000)

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message cannot be empty.")

        return value


class ReactionSerializer(serializers.Serializer):
    emoji = serializers.ChoiceField(choices=Reaction.Emoji.choices)
