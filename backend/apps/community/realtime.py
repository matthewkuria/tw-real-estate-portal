"""
Pushes community events to connected WebSocket clients.

Group layout:
- community.all          every connected user (content with no estate)
- community.estate.<id>  members with a plot in that estate
- community.staff        staff, who see every estate
- thread.<id>            clients currently viewing that thread
- user.<id>              every connection of one user (notifications)
"""

import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction


logger = logging.getLogger(__name__)

ALL_GROUP = "community.all"
STAFF_GROUP = "community.staff"


def user_group(user_id):
    return f"user.{user_id}"


def estate_group(estate_id):
    return f"community.estate.{estate_id}"


def thread_group(thread_id):
    return f"thread.{thread_id}"


def audience_groups(*estate_ids):
    """
    Groups that can see content tagged with any of these estates.
    """
    groups = set()

    for estate_id in estate_ids:
        if estate_id is None:
            groups.add(ALL_GROUP)
        else:
            groups.add(estate_group(estate_id))
            groups.add(STAFF_GROUP)

    return groups


def broadcast(groups, event_type, data):
    """
    Send an event to each group once the current transaction commits, so
    clients never hear about rows that were rolled back.
    """
    channel_layer = get_channel_layer()

    if channel_layer is None:
        return

    # Round-trip through JSON so the payload is plain types the channel
    # layer can serialise (e.g. for Redis).
    payload = json.loads(
        json.dumps({"type": event_type, "data": data}, cls=DjangoJSONEncoder)
    )
    message = {"type": "community.event", "payload": payload}

    def send():
        # The data is already committed; a push failure (e.g. Redis down)
        # must not turn the request into an error. Clients catch up via
        # the REST API.
        for group in groups:
            try:
                async_to_sync(channel_layer.group_send)(group, message)
            except Exception:
                logger.exception("Failed to push %s to %s", event_type, group)

    transaction.on_commit(send)
