from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.accounts.ws_auth import JWTAuthMiddleware
from apps.estates.models import Estate
from apps.properties.models import Property

from .models import Message, Thread, Update
from .routing import websocket_urlpatterns


IN_MEMORY_LAYER = override_settings(
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
    # The default hasher is deliberately slow; fixtures create many users.
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)


def make_user(email, role=User.Role.MEMBER, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=User.Status.ACTIVE,
        is_active=True,
        first_name=email.split("@")[0].title(),
        **extra,
    )


def make_estate(code):
    return Estate.objects.create(name=f"Estate {code}", code=code, location="Nairobi")


def allocate(user, estate, plot="1"):
    return Property.objects.create(
        estate=estate,
        plot_number=plot,
        price=1000,
        required_deposit=100,
        allocated_to=user,
        status=Property.Status.ALLOCATED,
    )


class CommunityFixtures:
    """
    Two estates. `resident` holds a plot in Estate A, `outsider` holds
    nothing. Staff of each relevant role.
    """

    def create_fixtures(self):
        self.estate_a = make_estate("a")
        self.estate_b = make_estate("b")

        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.resident = make_user("resident@example.com")
        self.outsider = make_user("outsider@example.com")

        allocate(self.resident, self.estate_a)


@IN_MEMORY_LAYER
class CommunityAPITestCase(CommunityFixtures, APITestCase):
    def setUp(self):
        cache.clear()
        self.create_fixtures()

    def as_user(self, user):
        self.client.force_authenticate(user)

    def ids(self, response):
        return {item["id"] for item in response.json()["data"]["results"]}


# ============================================================================
# UPDATES
# ============================================================================

class UpdateTests(CommunityAPITestCase):
    url = reverse("community:update-list")

    def setUp(self):
        super().setUp()
        self.everyone = Update.objects.create(title="All", body="x", author=self.admin)
        self.for_a = Update.objects.create(
            title="A", body="x", author=self.admin, estate=self.estate_a,
        )
        self.for_b = Update.objects.create(
            title="B", body="x", author=self.admin, estate=self.estate_b,
        )

    def test_admin_posts_update(self):
        self.as_user(self.admin)

        response = self.client.post(
            self.url,
            {"title": "Water outage", "body": "Tuesday 9-5", "estate": self.estate_a.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()["data"]
        self.assertEqual(body["author"]["id"], self.admin.pk)
        self.assertEqual(body["estate_name"], "Estate a")
        self.assertNotIn("email", body["author"])

    def test_non_admins_cannot_post(self):
        for user in (self.manager, self.resident):
            with self.subTest(role=user.role):
                self.as_user(user)

                response = self.client.post(
                    self.url,
                    {"title": "x", "body": "x"},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_sees_global_and_own_estate_only(self):
        self.as_user(self.resident)

        response = self.client.get(self.url)

        self.assertEqual(self.ids(response), {self.everyone.pk, self.for_a.pk})

    def test_member_without_plot_sees_global_only(self):
        self.as_user(self.outsider)

        response = self.client.get(self.url)

        self.assertEqual(self.ids(response), {self.everyone.pk})

    def test_staff_see_every_estate(self):
        self.as_user(self.finance)

        response = self.client.get(self.url)

        self.assertEqual(
            self.ids(response),
            {self.everyone.pk, self.for_a.pk, self.for_b.pk},
        )

    def test_other_estate_update_is_hidden(self):
        self.as_user(self.resident)

        response = self.client.get(
            reverse("community:update-detail", args=[self.for_b.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filter_by_estate(self):
        self.as_user(self.admin)

        response = self.client.get(self.url, {"estate": self.estate_b.pk})

        self.assertEqual(self.ids(response), {self.for_b.pk})

    def test_pinned_updates_come_first(self):
        Update.objects.filter(pk=self.everyone.pk).update(is_pinned=True)
        self.as_user(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.json()["data"]["results"][0]["id"], self.everyone.pk)

    def test_admin_edits_and_deletes(self):
        self.as_user(self.admin)
        url = reverse("community:update-detail", args=[self.everyone.pk])

        response = self.client.patch(url, {"is_pinned": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()["data"]["is_pinned"])

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Update.objects.filter(pk=self.everyone.pk).exists())

    def test_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ============================================================================
# THREADS
# ============================================================================

class ThreadTests(CommunityAPITestCase):
    url = reverse("community:thread-list")

    def create_thread(self, user, **data):
        self.as_user(user)
        payload = {"title": "Gate repairs", "body": "Who else noticed?"}
        payload.update(data)
        return self.client.post(self.url, payload, format="json")

    def detail(self, thread_id, name="detail"):
        return reverse(f"community:thread-{name}", args=[thread_id])

    def test_member_starts_thread_with_opening_message(self):
        response = self.create_thread(self.resident)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()["data"]
        self.assertEqual(body["message_count"], 1)
        self.assertEqual(body["author"]["id"], self.resident.pk)
        self.assertEqual(
            Message.objects.get(thread_id=body["id"]).body,
            "Who else noticed?",
        )

    def test_opening_message_is_required(self):
        response = self.create_thread(self.resident, body="   ")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("body", response.json())

    def test_member_can_only_tag_own_estates(self):
        response = self.create_thread(self.resident, estate=self.estate_b.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.create_thread(self.resident, estate=self.estate_a.pk)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_estate_threads_hidden_from_outsiders(self):
        thread_id = self.create_thread(
            self.resident,
            estate=self.estate_a.pk,
        ).json()["data"]["id"]

        self.as_user(self.outsider)
        self.assertNotIn(thread_id, self.ids(self.client.get(self.url)))
        self.assertEqual(
            self.client.get(self.detail(thread_id)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        self.as_user(self.finance)
        self.assertIn(thread_id, self.ids(self.client.get(self.url)))

    def test_author_and_moderators_can_rename(self):
        thread_id = self.create_thread(self.resident).json()["data"]["id"]

        self.as_user(self.outsider)
        response = self.client.patch(self.detail(thread_id), {"title": "x"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        for user in (self.resident, self.manager):
            with self.subTest(role=user.role):
                self.as_user(user)
                response = self.client.patch(
                    self.detail(thread_id),
                    {"title": f"Renamed by {user.pk}"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json()["data"]["title"], f"Renamed by {user.pk}")

    def test_estate_cannot_be_changed(self):
        thread_id = self.create_thread(self.resident).json()["data"]["id"]

        response = self.client.patch(
            self.detail(thread_id),
            {"estate": self.estate_a.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_moderators_pin_and_lock(self):
        thread_id = self.create_thread(self.resident).json()["data"]["id"]

        for name in ("pin", "lock"):
            with self.subTest(action=name):
                self.as_user(self.resident)
                response = self.client.post(self.detail(thread_id, name))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

                self.as_user(self.manager)
                response = self.client.post(self.detail(thread_id, name))
                self.assertEqual(response.status_code, status.HTTP_200_OK)

        thread = Thread.objects.get(pk=thread_id)
        self.assertTrue(thread.is_pinned)
        self.assertTrue(thread.is_locked)

    def test_delete_by_author_or_moderator(self):
        first = self.create_thread(self.resident).json()["data"]["id"]
        second = self.create_thread(self.resident).json()["data"]["id"]

        self.as_user(self.outsider)
        self.assertEqual(
            self.client.delete(self.detail(first)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.as_user(self.resident)
        self.assertEqual(
            self.client.delete(self.detail(first)).status_code,
            status.HTTP_200_OK,
        )

        self.as_user(self.admin)
        self.assertEqual(
            self.client.delete(self.detail(second)).status_code,
            status.HTTP_200_OK,
        )

    def test_search_by_title(self):
        self.create_thread(self.resident, title="Borehole pump")
        self.create_thread(self.resident, title="Security lights")

        response = self.client.get(self.url, {"search": "pump"})

        titles = [t["title"] for t in response.json()["data"]["results"]]
        self.assertEqual(titles, ["Borehole pump"])

    def test_active_threads_sort_first(self):
        old = self.create_thread(self.resident, title="Old").json()["data"]["id"]
        self.create_thread(self.resident, title="New")
        Thread.objects.filter(pk=old).update(
            last_message_at=timezone.now() + timedelta(minutes=1),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.json()["data"]["results"][0]["id"], old)


# ============================================================================
# MESSAGES
# ============================================================================

class MessageTests(CommunityAPITestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(title="Chat", author=self.resident)
        self.messages_url = reverse("community:thread-messages", args=[self.thread.pk])

    def post(self, user, body="Hello", **extra):
        self.as_user(user)
        return self.client.post(
            self.messages_url,
            {"body": body, **extra},
            format="json",
        )

    def message_url(self, message_id, name="detail"):
        return reverse(f"community:message-{name}", args=[message_id])

    def test_post_message_bumps_thread(self):
        before = self.thread.last_message_at

        response = self.post(self.outsider, "Count me in")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["data"]["body"], "Count me in")
        self.thread.refresh_from_db()
        self.assertGreater(self.thread.last_message_at, before)

    def test_empty_message_rejected(self):
        response = self.post(self.resident, "   ")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_locked_thread_blocks_members_not_moderators(self):
        Thread.objects.filter(pk=self.thread.pk).update(is_locked=True)

        self.assertEqual(
            self.post(self.resident).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.post(self.manager).status_code,
            status.HTTP_201_CREATED,
        )

    def test_reply_must_be_in_same_thread(self):
        other = Thread.objects.create(title="Other", author=self.resident)
        foreign = Message.objects.create(thread=other, author=self.resident, body="x")
        local = Message.objects.create(thread=self.thread, author=self.resident, body="x")

        response = self.post(self.outsider, reply_to=foreign.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.post(self.outsider, reply_to=local.pk)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["data"]["reply_to"], local.pk)

    def test_cannot_post_in_hidden_thread(self):
        hidden = Thread.objects.create(
            title="A only",
            author=self.resident,
            estate=self.estate_a,
        )
        self.as_user(self.outsider)

        response = self.client.post(
            reverse("community:thread-messages", args=[hidden.pk]),
            {"body": "Hi"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_history_paging(self):
        ids = [
            Message.objects.create(thread=self.thread, author=self.resident, body=str(i)).pk
            for i in range(5)
        ]
        self.as_user(self.resident)

        latest = self.client.get(self.messages_url, {"limit": 2}).json()["data"]
        self.assertEqual([m["id"] for m in latest["results"]], ids[3:])
        self.assertTrue(latest["has_more"])

        older = self.client.get(
            self.messages_url,
            {"limit": 2, "before": ids[3]},
        ).json()["data"]
        self.assertEqual([m["id"] for m in older["results"]], ids[1:3])

        oldest = self.client.get(
            self.messages_url,
            {"limit": 2, "before": ids[1]},
        ).json()["data"]
        self.assertEqual([m["id"] for m in oldest["results"]], ids[:1])
        self.assertFalse(oldest["has_more"])

        newer = self.client.get(self.messages_url, {"after": ids[2]}).json()["data"]
        self.assertEqual([m["id"] for m in newer["results"]], ids[3:])
        self.assertFalse(newer["has_more"])

    def test_bad_paging_params(self):
        self.as_user(self.resident)

        response = self.client.get(self.messages_url, {"before": "abc"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_author_can_edit(self):
        message_id = self.post(self.resident, "Original").json()["data"]["id"]

        for user in (self.outsider, self.admin):
            with self.subTest(role=user.role):
                self.as_user(user)
                response = self.client.patch(
                    self.message_url(message_id),
                    {"body": "Hacked"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.as_user(self.resident)
        response = self.client.patch(
            self.message_url(message_id),
            {"body": "Edited"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["data"]["body"], "Edited")
        self.assertIsNotNone(response.json()["data"]["edited_at"])

    def test_delete_hides_text_and_drops_from_count(self):
        message_id = self.post(self.outsider, "Oops").json()["data"]["id"]

        self.as_user(self.resident)
        response = self.client.delete(self.message_url(message_id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.as_user(self.outsider)
        response = self.client.delete(self.message_url(message_id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        message = self.client.get(self.message_url(message_id)).json()["data"]
        self.assertTrue(message["is_deleted"])
        self.assertEqual(message["body"], "")

        thread = self.client.get(
            reverse("community:thread-detail", args=[self.thread.pk])
        ).json()["data"]
        self.assertEqual(thread["message_count"], 0)

        response = self.client.patch(
            self.message_url(message_id),
            {"body": "Back"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_moderator_can_delete_any_message(self):
        message_id = self.post(self.outsider, "Spam").json()["data"]["id"]
        self.as_user(self.manager)

        response = self.client.delete(self.message_url(message_id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_reaction_toggles(self):
        message_id = self.post(self.resident).json()["data"]["id"]
        url = self.message_url(message_id, "react")

        self.as_user(self.outsider)
        response = self.client.post(url, {"emoji": "like"}, format="json")
        self.assertEqual(response.json()["data"]["reactions"], {"like": 1})
        self.assertEqual(response.json()["data"]["my_reactions"], ["like"])

        response = self.client.post(url, {"emoji": "like"}, format="json")
        self.assertEqual(response.json()["data"]["reactions"], {})
        self.assertEqual(response.json()["data"]["my_reactions"], [])

    def test_invalid_emoji(self):
        message_id = self.post(self.resident).json()["data"]["id"]

        response = self.client.post(
            self.message_url(message_id, "react"),
            {"emoji": "rocket"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_posting_is_rate_limited(self):
        for i in range(30):
            self.post(self.resident, f"Message {i}")

        response = self.post(self.resident, "One too many")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ============================================================================
# RESPONSE ENVELOPE
# ============================================================================

class EnvelopeTests(CommunityAPITestCase):
    """
    Responses use the same envelope as the accounts app.
    """

    def assertEnvelope(self, response, code, success=True):
        body = response.json()
        self.assertEqual(
            set(body),
            {"success", "code", "description", "data"},
        )
        self.assertIs(body["success"], success)
        self.assertEqual(body["code"], code)
        return body["data"]

    def test_list_wraps_paginated_page(self):
        Update.objects.create(title="All", body="x", author=self.admin)
        self.as_user(self.resident)

        data = self.assertEnvelope(
            self.client.get(reverse("community:update-list")),
            "S013",
        )

        self.assertEqual(set(data), {"count", "next", "previous", "results"})
        self.assertEqual(data["count"], 1)

    def test_success_codes(self):
        self.as_user(self.admin)

        response = self.client.post(
            reverse("community:thread-list"),
            {"title": "Hi", "body": "First"},
            format="json",
        )
        thread_id = self.assertEnvelope(response, "S020")["id"]

        response = self.client.post(
            reverse("community:thread-messages", args=[thread_id]),
            {"body": "Second"},
            format="json",
        )
        message_id = self.assertEnvelope(response, "S028")["id"]

        response = self.client.delete(
            reverse("community:message-detail", args=[message_id])
        )
        self.assertIsNone(self.assertEnvelope(response, "S031"))

    def test_error_codes(self):
        thread = Thread.objects.create(title="Locked", author=self.admin, is_locked=True)
        self.as_user(self.resident)

        response = self.client.post(
            reverse("community:thread-messages", args=[thread.pk]),
            {"body": "Let me in"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIsNone(self.assertEnvelope(response, "E008", success=False))


# ============================================================================
# WEBSOCKETS
# ============================================================================

@IN_MEMORY_LAYER
class WebSocketTests(CommunityFixtures, TransactionTestCase):
    """
    TransactionTestCase so data written by the test is visible to the
    consumer's database calls, and on_commit broadcasts actually fire.
    """

    application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))

    def setUp(self):
        cache.clear()
        self.create_fixtures()

    async def connect(self, user):
        token = str(AccessToken.for_user(user))
        communicator = WebsocketCommunicator(
            self.application,
            f"/ws/community/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    @sync_to_async
    def api(self, user, method, url, data=None):
        client = APIClient()
        client.force_authenticate(user)
        return getattr(client, method)(url, data or {}, format="json")

    @sync_to_async
    def create_thread(self, **fields):
        return Thread.objects.create(author=self.resident, **fields)

    async def test_rejects_missing_or_invalid_token(self):
        for path in ("/ws/community/", "/ws/community/?token=garbage"):
            with self.subTest(path=path):
                communicator = WebsocketCommunicator(self.application, path)
                connected, code = await communicator.connect()

                self.assertFalse(connected)
                self.assertEqual(code, 4401)

    async def test_rejects_foreign_origin(self):
        from config.asgi import application

        token = str(AccessToken.for_user(self.resident))
        communicator = WebsocketCommunicator(
            application,
            f"/ws/community/?token={token}",
            headers=[(b"origin", b"https://evil.example.com")],
        )

        connected, _ = await communicator.connect()

        self.assertFalse(connected)

    async def test_updates_reach_only_their_audience(self):
        resident = await self.connect(self.resident)
        outsider = await self.connect(self.outsider)

        await self.api(
            self.admin,
            "post",
            reverse("community:update-list"),
            {"title": "Estate A meeting", "body": "x", "estate": self.estate_a.pk},
        )

        event = await resident.receive_json_from()
        self.assertEqual(event["type"], "update.created")
        self.assertEqual(event["data"]["title"], "Estate A meeting")
        self.assertTrue(await outsider.receive_nothing())

        await self.api(
            self.admin,
            "post",
            reverse("community:update-list"),
            {"title": "For everyone", "body": "x"},
        )

        for communicator in (resident, outsider):
            event = await communicator.receive_json_from()
            self.assertEqual(event["data"]["title"], "For everyone")

        await resident.disconnect()
        await outsider.disconnect()

    async def test_thread_subscribers_get_messages_live(self):
        thread = await self.create_thread(title="Chat")
        viewer = await self.connect(self.outsider)

        await viewer.send_json_to({"action": "subscribe", "thread": thread.pk})
        self.assertEqual(
            await viewer.receive_json_from(),
            {"type": "subscribed", "thread": thread.pk},
        )

        await self.api(
            self.resident,
            "post",
            reverse("community:thread-messages", args=[thread.pk]),
            {"body": "Live!"},
        )

        events = {}
        for _ in range(2):
            event = await viewer.receive_json_from()
            events[event["type"]] = event["data"]

        self.assertEqual(events["message.created"]["body"], "Live!")
        self.assertEqual(events["thread.activity"]["id"], thread.pk)

        await viewer.disconnect()

    async def test_reactions_and_deletes_are_pushed(self):
        thread = await self.create_thread(title="Chat")
        message = await sync_to_async(Message.objects.create)(
            thread=thread,
            author=self.resident,
            body="Hi",
        )
        viewer = await self.connect(self.resident)
        await viewer.send_json_to({"action": "subscribe", "thread": thread.pk})
        await viewer.receive_json_from()

        await self.api(
            self.outsider,
            "post",
            reverse("community:message-react", args=[message.pk]),
            {"emoji": "love"},
        )
        event = await viewer.receive_json_from()
        self.assertEqual(event["type"], "reaction.updated")
        self.assertEqual(event["data"]["reactions"], {"love": 1})

        await self.api(
            self.resident,
            "delete",
            reverse("community:message-detail", args=[message.pk]),
        )
        event = await viewer.receive_json_from()
        self.assertEqual(event["type"], "message.deleted")
        self.assertEqual(event["data"]["id"], message.pk)

        await viewer.disconnect()

    async def test_cannot_subscribe_to_hidden_thread(self):
        thread = await self.create_thread(title="A only", estate=self.estate_a)
        outsider = await self.connect(self.outsider)

        await outsider.send_json_to({"action": "subscribe", "thread": thread.pk})

        event = await outsider.receive_json_from()
        self.assertEqual(event["type"], "error")

        await outsider.disconnect()

    async def test_typing_goes_to_others_not_sender(self):
        thread = await self.create_thread(title="Chat")
        typer = await self.connect(self.resident)
        watcher = await self.connect(self.outsider)

        for communicator in (typer, watcher):
            await communicator.send_json_to({"action": "subscribe", "thread": thread.pk})
            await communicator.receive_json_from()

        await typer.send_json_to({"action": "typing", "thread": thread.pk})

        event = await watcher.receive_json_from()
        self.assertEqual(event["type"], "typing")
        self.assertEqual(event["data"]["user"]["id"], self.resident.pk)
        self.assertTrue(await typer.receive_nothing())

        await typer.disconnect()
        await watcher.disconnect()

    async def test_unknown_action(self):
        communicator = await self.connect(self.resident)

        await communicator.send_json_to({"action": "explode"})

        event = await communicator.receive_json_from()
        self.assertEqual(event, {"type": "error", "detail": "Unknown action."})

        await communicator.disconnect()
