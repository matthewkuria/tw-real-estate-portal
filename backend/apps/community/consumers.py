"""
One WebSocket per signed-in user: ws/community/?token=<access JWT>

On connect the client automatically receives update.* and thread.*
events for everything it can see, plus its own notification.* events.
To follow a thread's conversation it sends:

    {"action": "subscribe", "thread": 12}
    {"action": "unsubscribe", "thread": 12}
    {"action": "typing", "thread": 12}

and then receives message.*, reaction.updated and typing events for that
thread. Posting messages goes through the REST API.
"""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .access import member_estate_ids, sees_all_estates, visible_to
from .models import Thread
from .realtime import (
    ALL_GROUP,
    STAFF_GROUP,
    estate_group,
    thread_group,
    user_group,
)


# Application close codes (4000-4999 are free for app use).
CLOSE_UNAUTHENTICATED = 4401


class CommunityConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")

        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        self.user = user
        self.threads = set()
        self.audience = await self._audience_groups()

        for group in self.audience:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()

    async def disconnect(self, code):
        for group in getattr(self, "audience", set()) | {
            thread_group(thread_id) for thread_id in getattr(self, "threads", set())
        }:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        thread_id = content.get("thread")

        if action not in {"subscribe", "unsubscribe", "typing"}:
            await self._error("Unknown action.")
            return

        if not isinstance(thread_id, int):
            await self._error("'thread' must be a thread id.")
            return

        if action == "subscribe":
            await self._subscribe(thread_id)
        elif action == "unsubscribe":
            await self._unsubscribe(thread_id)
        else:
            await self._typing(thread_id)

    # ------------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------------

    async def _subscribe(self, thread_id):
        if not await self._can_view_thread(thread_id):
            await self._error("Thread not found.", thread=thread_id)
            return

        self.threads.add(thread_id)
        await self.channel_layer.group_add(
            thread_group(thread_id),
            self.channel_name,
        )
        await self.send_json({"type": "subscribed", "thread": thread_id})

    async def _unsubscribe(self, thread_id):
        self.threads.discard(thread_id)
        await self.channel_layer.group_discard(
            thread_group(thread_id),
            self.channel_name,
        )
        await self.send_json({"type": "unsubscribed", "thread": thread_id})

    async def _typing(self, thread_id):
        if thread_id not in self.threads:
            await self._error("Subscribe to the thread first.", thread=thread_id)
            return

        await self.channel_layer.group_send(
            thread_group(thread_id),
            {
                "type": "community.typing",
                "sender": self.channel_name,
                "thread": thread_id,
                "user": {
                    "id": self.user.pk,
                    "name": self.user.get_full_name() or "Member",
                },
            },
        )

    # ------------------------------------------------------------------------
    # Group event handlers
    # ------------------------------------------------------------------------

    async def community_event(self, event):
        await self.send_json(event["payload"])

    async def community_typing(self, event):
        if event["sender"] == self.channel_name:
            return

        await self.send_json(
            {
                "type": "typing",
                "data": {"thread": event["thread"], "user": event["user"]},
            }
        )

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    async def _error(self, detail, **extra):
        await self.send_json({"type": "error", "detail": detail, **extra})

    @database_sync_to_async
    def _audience_groups(self):
        groups = {ALL_GROUP, user_group(self.user.pk)}

        if sees_all_estates(self.user):
            groups.add(STAFF_GROUP)
        else:
            groups.update(
                estate_group(estate_id)
                for estate_id in member_estate_ids(self.user)
            )

        return groups

    @database_sync_to_async
    def _can_view_thread(self, thread_id):
        return visible_to(
            Thread.objects.filter(pk=thread_id),
            self.user,
        ).exists()
