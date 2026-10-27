from datetime import timedelta
from smtplib import SMTPException
from unittest import mock

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core import mail
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.accounts.services import approve_member, reactivate_member, suspend_member
from apps.accounts.ws_auth import JWTAuthMiddleware
from apps.community.models import Message, Thread
from apps.community.routing import websocket_urlpatterns
from apps.estates.models import Estate
from apps.payments.models import Payment
from apps.properties.models import Property

from .models import EmailOutbox, Notification, NotificationPreference
from .services import send_pending_emails
from .types import NotificationType as T


TEST_SETTINGS = override_settings(
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)


def make_user(email, role=User.Role.MEMBER, status=User.Status.ACTIVE, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=status,
        is_active=status == User.Status.ACTIVE,
        first_name=email.split("@")[0].title(),
        **extra,
    )


class Fixtures:
    def create_fixtures(self):
        self.estate_a = Estate.objects.create(name="Estate A", code="a", location="Nairobi")
        self.estate_b = Estate.objects.create(name="Estate B", code="b", location="Nairobi")

        self.super_admin = make_user("super@example.com", User.Role.SUPER_ADMIN)
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.resident = make_user("resident@example.com")
        self.outsider = make_user("outsider@example.com")

        Property.objects.create(
            estate=self.estate_a,
            plot_number="1",
            price=1000,
            required_deposit=100,
            allocated_to=self.resident,
            status=Property.Status.ALLOCATED,
        )

        # Start every test with an empty inbox.
        Notification.objects.all().delete()
        EmailOutbox.objects.all().delete()


@TEST_SETTINGS
class NotificationTestCase(Fixtures, APITestCase):
    def setUp(self):
        cache.clear()
        self.create_fixtures()

    def as_user(self, user):
        self.client.force_authenticate(user)

    def recipients(self, type):
        return set(
            Notification.objects
            .filter(type=type)
            .values_list("recipient__email", flat=True)
        )

    def emailed(self, type):
        return set(
            EmailOutbox.objects
            .filter(notification__type=type)
            .values_list("to_email", flat=True)
        )


# ============================================================================
# EVENTS
# ============================================================================

class CommunityUpdateTests(NotificationTestCase):
    def post_update(self, **data):
        self.as_user(self.admin)
        return self.client.post(
            reverse("community:update-list"),
            {"title": "Water outage", "body": "Tuesday 9-5", **data},
            format="json",
        )

    def test_estate_update_notifies_its_audience_only(self):
        self.post_update(estate=self.estate_a.pk)

        self.assertEqual(
            self.recipients(T.UPDATE_POSTED),
            {
                "resident@example.com",
                "super@example.com",
                "manager@example.com",
                "finance@example.com",
            },
        )

    def test_global_update_notifies_everyone_but_the_author(self):
        self.post_update()

        recipients = self.recipients(T.UPDATE_POSTED)
        self.assertIn("outsider@example.com", recipients)
        self.assertNotIn("admin@example.com", recipients)

    def test_update_is_emailed_with_link(self):
        self.post_update()

        email = EmailOutbox.objects.get(to_email="resident@example.com")
        self.assertEqual(email.subject, "New update: Water outage")
        self.assertIn("http://localhost:3000/community/updates/", email.body)
        self.assertIn("/settings/notifications", email.body)

    def test_opted_out_users_get_no_email(self):
        NotificationPreference.objects.create(
            user=self.resident,
            type=T.UPDATE_POSTED,
            email_enabled=False,
        )

        self.post_update()

        self.assertIn("resident@example.com", self.recipients(T.UPDATE_POSTED))
        self.assertNotIn("resident@example.com", self.emailed(T.UPDATE_POSTED))

    def test_pending_members_are_not_notified(self):
        make_user("waiting@example.com", status=User.Status.PENDING)

        self.post_update()

        self.assertNotIn("waiting@example.com", self.recipients(T.UPDATE_POSTED))


class ForumTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(title="Gate", author=self.resident)
        self.opening = Message.objects.create(
            thread=self.thread,
            author=self.resident,
            body="Who else noticed?",
        )

    def post(self, user, body="Me too", **extra):
        self.as_user(user)
        return self.client.post(
            reverse("community:thread-messages", args=[self.thread.pk]),
            {"body": body, **extra},
            format="json",
        )

    def test_reply_notifies_the_replied_to_author(self):
        reply_target = Message.objects.create(
            thread=self.thread,
            author=self.outsider,
            body="Since Monday",
        )

        self.post(self.manager, "Fixing it today", reply_to=reply_target.pk)

        notification = Notification.objects.get(type=T.FORUM_REPLY)
        self.assertEqual(notification.recipient, self.outsider)
        self.assertEqual(notification.actor, self.manager)
        self.assertEqual(
            notification.link,
            f"/community/threads/{self.thread.pk}?message={notification.data['message']}",
        )

    def test_thread_starter_gets_one_grouped_notification(self):
        self.post(self.outsider, "First")
        self.post(self.manager, "Second")

        notifications = Notification.objects.filter(
            recipient=self.resident,
            type=T.FORUM_THREAD_ACTIVITY,
        )
        self.assertEqual(notifications.count(), 1)

        notification = notifications.get()
        self.assertEqual(notification.data["count"], 2)
        self.assertEqual(notification.title, "2 new messages in “Gate”")
        self.assertIn("Second", notification.body)

    def test_new_group_starts_after_reading(self):
        self.post(self.outsider, "First")
        Notification.objects.update(read_at=timezone.now())

        self.post(self.outsider, "Second")

        self.assertEqual(
            Notification.objects.filter(type=T.FORUM_THREAD_ACTIVITY).count(),
            2,
        )

    def test_reply_to_starter_is_not_also_counted_as_activity(self):
        self.post(self.outsider, "Answer", reply_to=self.opening.pk)

        self.assertEqual(self.recipients(T.FORUM_REPLY), {"resident@example.com"})
        self.assertEqual(self.recipients(T.FORUM_THREAD_ACTIVITY), set())

    def test_no_notification_for_own_activity(self):
        self.post(self.resident, "Replying to myself", reply_to=self.opening.pk)

        self.assertFalse(Notification.objects.exists())

    def test_reaction_notifies_author_in_app_only(self):
        self.as_user(self.outsider)
        url = reverse("community:message-react", args=[self.opening.pk])

        self.client.post(url, {"emoji": "like"}, format="json")

        notification = Notification.objects.get(type=T.FORUM_REACTION)
        self.assertEqual(notification.recipient, self.resident)
        self.assertIn("👍", notification.title)
        self.assertFalse(EmailOutbox.objects.exists())

        # Removing the reaction doesn't notify again.
        self.client.post(url, {"emoji": "like"}, format="json")
        self.assertEqual(Notification.objects.count(), 1)

    def test_forum_activity_is_never_emailed(self):
        self.post(self.outsider)

        self.assertFalse(EmailOutbox.objects.exists())


class AccountTests(NotificationTestCase):
    def test_registration_notifies_approvers(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "email": "new@example.com",
                "password": "Pass-word-123!",
                "password_confirmation": "Pass-word-123!",
                "first_name": "New",
                "last_name": "Member",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(
            self.recipients(T.REGISTRATION_PENDING),
            {"super@example.com", "admin@example.com", "manager@example.com"},
        )
        self.assertEqual(
            self.emailed(T.REGISTRATION_PENDING),
            {"super@example.com", "admin@example.com", "manager@example.com"},
        )

    def test_status_changes_notify_and_always_email(self):
        user = make_user("joiner@example.com", status=User.Status.PENDING)

        # Even an opt-out row can't silence account emails.
        NotificationPreference.objects.create(
            user=user,
            type=T.ACCOUNT_SUSPENDED,
            email_enabled=False,
        )

        approve_member(user)
        suspend_member(user)
        reactivate_member(user)

        types = list(
            Notification.objects
            .filter(recipient=user)
            .order_by("id")
            .values_list("type", flat=True)
        )
        self.assertEqual(
            types,
            [T.ACCOUNT_APPROVED, T.ACCOUNT_SUSPENDED, T.ACCOUNT_REACTIVATED],
        )
        self.assertEqual(
            EmailOutbox.objects.filter(to_email="joiner@example.com").count(),
            3,
        )

    def test_status_change_outside_services_is_caught(self):
        user = make_user("joiner@example.com", status=User.Status.PENDING)

        user.status = User.Status.REJECTED
        user.save()

        self.assertEqual(self.recipients(T.ACCOUNT_REJECTED), {"joiner@example.com"})

    def test_saves_that_dont_change_status_are_quiet(self):
        self.resident.first_name = "Renamed"
        self.resident.save()
        self.resident.save(update_fields=["last_name"])

        self.assertFalse(Notification.objects.exists())


class PaymentTests(NotificationTestCase):
    def make_payment(self, **fields):
        return Payment.objects.create(
            member=self.resident,
            estate=self.estate_a,
            amount=15000,
            method=Payment.Method.MPESA,
            reference="QX12AB",
            recorded_by=self.finance,
            **fields,
        )

    def test_pending_payment_is_quiet(self):
        self.make_payment()

        self.assertFalse(Notification.objects.exists())

    def test_confirmation_notifies_member(self):
        payment = self.make_payment()

        payment.status = Payment.Status.CONFIRMED
        payment.confirmed_by = self.finance
        payment.confirmed_at = timezone.now()
        payment.save()

        notification = Notification.objects.get(type=T.PAYMENT_CONFIRMED)
        self.assertEqual(notification.recipient, self.resident)
        self.assertEqual(notification.title, "Payment of KES 15,000.00 confirmed")
        self.assertIn("QX12AB", notification.body)
        self.assertEqual(notification.actor, self.finance)
        self.assertEqual(self.emailed(T.PAYMENT_CONFIRMED), {"resident@example.com"})

    def test_rejection_and_reversal(self):
        for payment_status, notification_type in [
            (Payment.Status.REJECTED, T.PAYMENT_REJECTED),
            (Payment.Status.REVERSED, T.PAYMENT_REVERSED),
        ]:
            with self.subTest(status=payment_status):
                payment = self.make_payment()
                payment.status = payment_status
                payment.save()

                self.assertEqual(
                    self.recipients(notification_type),
                    {"resident@example.com"},
                )


# ============================================================================
# API
# ============================================================================

class NotificationAPITests(NotificationTestCase):
    url = reverse("notifications:notification-list")

    def setUp(self):
        super().setUp()
        self.first = Notification.objects.create(
            recipient=self.resident, type=T.UPDATE_POSTED, title="One",
        )
        self.second = Notification.objects.create(
            recipient=self.resident, type=T.UPDATE_POSTED, title="Two",
        )
        self.someone_elses = Notification.objects.create(
            recipient=self.outsider, type=T.UPDATE_POSTED, title="Not yours",
        )
        self.as_user(self.resident)

    def test_list_is_own_newest_first_with_unread_count(self):
        response = self.client.get(self.url)

        body = response.json()
        self.assertEqual(body["code"], "S033")
        self.assertEqual(
            [n["title"] for n in body["data"]["results"]],
            ["Two", "One"],
        )
        self.assertEqual(body["data"]["unread_count"], 2)

    def test_unread_filter(self):
        Notification.objects.filter(pk=self.first.pk).update(read_at=timezone.now())

        response = self.client.get(self.url, {"unread": "true"})

        self.assertEqual(
            [n["id"] for n in response.json()["data"]["results"]],
            [self.second.pk],
        )

    def test_mark_one_read(self):
        response = self.client.post(
            reverse("notifications:notification-read", args=[self.first.pk])
        )

        self.assertEqual(response.json()["code"], "S035")
        self.assertEqual(response.json()["data"], {"marked": 1, "unread_count": 1})
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_read)

    def test_cannot_touch_others_notifications(self):
        response = self.client.post(
            reverse("notifications:notification-read", args=[self.someone_elses.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_mark_all_read_and_count(self):
        response = self.client.post(reverse("notifications:notification-read-all"))
        self.assertEqual(response.json()["data"]["marked"], 2)

        response = self.client.get(reverse("notifications:notification-unread-count"))
        self.assertEqual(response.json()["data"], {"unread_count": 0})

        self.someone_elses.refresh_from_db()
        self.assertFalse(self.someone_elses.is_read)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.url).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )


class PreferenceAPITests(NotificationTestCase):
    url = reverse("notifications:preferences")

    def setUp(self):
        super().setUp()
        self.as_user(self.resident)

    def preference(self, response, type):
        return next(p for p in response.json()["data"] if p["type"] == type)

    def test_lists_every_type_with_defaults(self):
        response = self.client.get(self.url)

        self.assertEqual(response.json()["code"], "S037")
        self.assertEqual(len(response.json()["data"]), len(T))
        self.assertEqual(
            self.preference(response, T.UPDATE_POSTED),
            {
                "type": "update_posted",
                "label": "New community update",
                "sends_email": True,
                "can_opt_out": True,
                "email_enabled": True,
            },
        )
        self.assertFalse(self.preference(response, T.FORUM_REPLY)["email_enabled"])

    def test_opt_out(self):
        response = self.client.patch(
            self.url,
            {"preferences": [{"type": "update_posted", "email_enabled": False}]},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S038")
        self.assertFalse(self.preference(response, T.UPDATE_POSTED)["email_enabled"])

    def test_cannot_opt_out_of_required_or_in_app_types(self):
        for notification_type in ("account_suspended", "forum_reply"):
            with self.subTest(type=notification_type):
                response = self.client.patch(
                    self.url,
                    {"preferences": [{"type": notification_type, "email_enabled": False}]},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertFalse(NotificationPreference.objects.exists())


# ============================================================================
# EMAIL WORKER
# ============================================================================

class EmailWorkerTests(NotificationTestCase):
    def queue(self, **fields):
        return EmailOutbox.objects.create(
            to_email="resident@example.com",
            subject="Hello",
            body="Body",
            **fields,
        )

    def test_sends_due_emails(self):
        email = self.queue()
        later = self.queue(next_attempt_at=timezone.now() + timedelta(hours=1))

        sent, failed = send_pending_emails()

        self.assertEqual((sent, failed), (1, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["resident@example.com"])

        email.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(email.status, EmailOutbox.Status.SENT)
        self.assertEqual(later.status, EmailOutbox.Status.PENDING)

    def test_failure_retries_with_backoff_then_gives_up(self):
        email = self.queue()

        with mock.patch(
            "apps.notifications.services.deliver",
            side_effect=SMTPException("server down"),
        ):
            send_pending_emails(max_attempts=2)
            email.refresh_from_db()
            self.assertEqual(email.status, EmailOutbox.Status.PENDING)
            self.assertEqual(email.attempts, 1)
            self.assertIn("server down", email.last_error)
            self.assertGreater(email.next_attempt_at, timezone.now() + timedelta(minutes=1))

            EmailOutbox.objects.update(next_attempt_at=timezone.now())
            send_pending_emails(max_attempts=2)
            email.refresh_from_db()
            self.assertEqual(email.status, EmailOutbox.Status.FAILED)

    def test_recovers_emails_from_a_crashed_worker(self):
        email = self.queue(
            status=EmailOutbox.Status.SENDING,
            claimed_at=timezone.now() - timedelta(hours=1),
        )

        send_pending_emails()

        email.refresh_from_db()
        self.assertEqual(email.status, EmailOutbox.Status.SENT)

    def test_management_command(self):
        from django.core.management import call_command

        self.queue()

        call_command("send_notifications", stdout=mock.MagicMock())

        self.assertEqual(len(mail.outbox), 1)


# ============================================================================
# LIVE PUSH
# ============================================================================

@TEST_SETTINGS
class LivePushTests(Fixtures, TransactionTestCase):
    application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))

    def setUp(self):
        cache.clear()
        self.create_fixtures()

    async def test_notification_reaches_recipients_socket(self):
        from asgiref.sync import sync_to_async

        token = str(AccessToken.for_user(self.resident))
        communicator = WebsocketCommunicator(
            self.application,
            f"/ws/community/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        @sync_to_async
        def post_update():
            client = APIClient()
            client.force_authenticate(self.admin)
            client.post(
                reverse("community:update-list"),
                {"title": "Meeting", "body": "Saturday"},
                format="json",
            )

        await post_update()

        events = {}
        for _ in range(2):
            event = await communicator.receive_json_from()
            events[event["type"]] = event["data"]

        self.assertEqual(events["update.created"]["title"], "Meeting")
        self.assertEqual(events["notification.created"]["title"], "New update: Meeting")
        self.assertFalse(events["notification.created"]["is_read"])

        await communicator.disconnect()
