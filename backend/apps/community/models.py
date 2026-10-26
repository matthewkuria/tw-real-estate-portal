from django.conf import settings
from django.db import models
from django.utils import timezone


class Update(models.Model):
    """
    An announcement posted by an admin. Visible to every member, or only
    to members with a plot in `estate` when one is set.
    """

    title = models.CharField(max_length=200)
    body = models.TextField()

    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.PROTECT,
        related_name="community_updates",
        null=True,
        blank=True,
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="community_updates",
        null=True,
    )

    is_pinned = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title


class Thread(models.Model):
    """
    A forum topic. Estate scoping works the same way as for updates.
    """

    title = models.CharField(max_length=200)

    estate = models.ForeignKey(
        "estates.Estate",
        on_delete=models.PROTECT,
        related_name="forum_threads",
        null=True,
        blank=True,
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="forum_threads",
        null=True,
    )

    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)

    # Bumped on every new message so active threads sort first.
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-last_message_at"]

    def __str__(self):
        return self.title


class Message(models.Model):
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="forum_messages",
        null=True,
    )

    body = models.TextField(max_length=4000)

    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="replies",
        null=True,
        blank=True,
    )

    edited_at = models.DateTimeField(null=True, blank=True)

    # Deleted messages keep their place in the conversation but lose
    # their text, so replies to them still make sense.
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["thread", "id"]),
        ]

    def __str__(self):
        return f"Message {self.pk} in {self.thread}"


class Reaction(models.Model):
    class Emoji(models.TextChoices):
        LIKE = "like", "👍"
        LOVE = "love", "❤️"
        LAUGH = "laugh", "😂"
        WOW = "wow", "😮"
        SAD = "sad", "😢"
        PRAY = "pray", "🙏"

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="reactions",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forum_reactions",
    )

    emoji = models.CharField(max_length=10, choices=Emoji.choices)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user", "emoji"],
                name="unique_reaction_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} {self.emoji} on {self.message_id}"
