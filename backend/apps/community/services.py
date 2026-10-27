from django.db import transaction
from django.utils import timezone

from apps.notifications import events as notification_events

from .models import Message, Reaction, Thread
from .realtime import audience_groups, broadcast, thread_group
from .serializers import MessageSerializer, ThreadSerializer, UpdateSerializer


class ThreadLocked(Exception):
    pass


def _message_data(message):
    return MessageSerializer(message).data


def _thread_data(thread):
    return ThreadSerializer(thread).data


# ============================================================================
# UPDATES
# ============================================================================

# Distinguishes "no previous estate given" from "previously visible to
# everyone" (estate None).
_NOT_MOVED = object()


def announce_update(update, event_type, previous_estate_id=_NOT_MOVED):
    """
    Push an update event to everyone who can see it. When an update moves
    between estates, both the old and new audiences are told.
    """
    estate_ids = [update.estate_id]

    if previous_estate_id is not _NOT_MOVED:
        estate_ids.append(previous_estate_id)

    groups = audience_groups(*estate_ids)
    broadcast(groups, event_type, UpdateSerializer(update).data)


def announce_update_deleted(update_id, estate_id):
    broadcast(
        audience_groups(estate_id),
        "update.deleted",
        {"id": update_id},
    )


# ============================================================================
# THREADS
# ============================================================================

@transaction.atomic
def create_thread(author, title, body, estate=None):
    thread = Thread.objects.create(
        author=author,
        title=title,
        estate=estate,
    )

    Message.objects.create(
        thread=thread,
        author=author,
        body=body,
    )

    broadcast(
        audience_groups(thread.estate_id),
        "thread.created",
        _thread_data(thread),
    )

    return thread


@transaction.atomic
def update_thread(thread, **fields):
    for name, value in fields.items():
        setattr(thread, name, value)

    thread.save()

    data = _thread_data(thread)
    broadcast(audience_groups(thread.estate_id), "thread.updated", data)
    broadcast([thread_group(thread.pk)], "thread.updated", data)

    return thread


@transaction.atomic
def delete_thread(thread):
    thread_id = thread.pk
    estate_id = thread.estate_id

    thread.delete()

    payload = {"id": thread_id}
    broadcast(audience_groups(estate_id), "thread.deleted", payload)
    broadcast([thread_group(thread_id)], "thread.deleted", payload)


# ============================================================================
# MESSAGES
# ============================================================================

@transaction.atomic
def post_message(thread, author, body, reply_to=None, *, as_moderator=False):
    # Lock the row so concurrent posts can't race a moderator locking it.
    thread = Thread.objects.select_for_update().get(pk=thread.pk)

    if thread.is_locked and not as_moderator:
        raise ThreadLocked("This thread is locked.")

    message = Message.objects.create(
        thread=thread,
        author=author,
        body=body,
        reply_to=reply_to,
    )

    thread.last_message_at = message.created_at
    thread.save(update_fields=["last_message_at", "updated_at"])

    broadcast(
        [thread_group(thread.pk)],
        "message.created",
        _message_data(message),
    )
    broadcast(
        audience_groups(thread.estate_id),
        "thread.activity",
        {
            "id": thread.pk,
            "last_message_at": thread.last_message_at,
        },
    )

    notification_events.message_posted(message)

    return message


@transaction.atomic
def edit_message(message, body):
    message.body = body
    message.edited_at = timezone.now()
    message.save(update_fields=["body", "edited_at"])

    broadcast(
        [thread_group(message.thread_id)],
        "message.updated",
        _message_data(message),
    )

    return message


@transaction.atomic
def delete_message(message):
    message.is_deleted = True
    message.body = ""
    message.save(update_fields=["is_deleted", "body"])

    message.reactions.all().delete()

    broadcast(
        [thread_group(message.thread_id)],
        "message.deleted",
        {"id": message.pk, "thread": message.thread_id},
    )

    return message


@transaction.atomic
def toggle_reaction(message, user, emoji):
    """
    Add the reaction, or remove it if the user already reacted with it.
    Returns True if the reaction is now present.
    """
    deleted, _ = Reaction.objects.filter(
        message=message,
        user=user,
        emoji=emoji,
    ).delete()

    if not deleted:
        reaction = Reaction.objects.create(message=message, user=user, emoji=emoji)
        notification_events.reaction_added(reaction)

    message = Message.objects.prefetch_related("reactions").get(pk=message.pk)

    broadcast(
        [thread_group(message.thread_id)],
        "reaction.updated",
        {
            "message": message.pk,
            "reactions": MessageSerializer().get_reactions(message),
            "user": user.pk,
            "emoji": emoji,
            "active": not deleted,
        },
    )

    return not deleted
