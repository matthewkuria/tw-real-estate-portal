import io
from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate
from apps.notifications.models import EmailOutbox
from apps.notifications.services import send_pending_emails
from apps.properties.models import Property

from . import services
from .content import sanitize, to_text
from .models import Delivery, Newsletter, Subscriber


FAST = override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    API_BASE_URL="https://api.example.com",
    FRONTEND_URL="https://portal.example.com",
    SITE_NAME="Oak Estates",
)

BODY = "<h2>October news</h2><p>The borehole is fixed. <a href=\"https://example.com/r\">Report</a></p>"


def make_user(email, role=User.Role.MEMBER, status=User.Status.ACTIVE, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=status,
        is_active=status == User.Status.ACTIVE,
        **extra,
    )


# ============================================================================
# CONTENT
# ============================================================================

class ContentTests(TestCase):
    def test_sanitize_strips_dangerous_markup(self):
        cleaned = sanitize(
            '<p onclick="steal()" style="color:red">Hi</p>'
            "<script>alert(1)</script>"
            '<iframe src="https://evil.example.com"></iframe>'
            '<a href="javascript:alert(1)">bad</a>'
            '<img src="https://cdn.example.com/a.jpg" onerror="x()">'
        )

        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("style", cleaned)
        self.assertNotIn("script", cleaned)
        self.assertNotIn("iframe", cleaned)
        self.assertNotIn("javascript", cleaned)
        self.assertNotIn("onerror", cleaned)
        self.assertIn("<p>Hi</p>", cleaned)
        self.assertIn('<img src="https://cdn.example.com/a.jpg">', cleaned)

    def test_links_get_safe_rel(self):
        self.assertIn('rel="noopener noreferrer"', sanitize('<a href="https://x.com">x</a>'))

    def test_plain_text_version(self):
        text = to_text(
            "<h2>News</h2><p>Line one<br>Line two &amp; more</p>"
            '<ul><li>First</li><li>Second</li></ul>'
            '<p><a href="https://example.com">Read</a></p>'
        )

        self.assertEqual(
            text,
            "News\n\nLine one\nLine two & more\n\n• First\n• Second\n\nRead (https://example.com)",
        )


# ============================================================================
# ADMIN API
# ============================================================================

@FAST
class NewsletterTestCase(APITestCase):
    list_url = reverse("newsletters:issue-list")

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN, first_name="Ada")
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.jane = make_user("jane@example.com", first_name="Jane")
        self.peter = make_user("peter@example.com", first_name="Peter")
        make_user("waiting@example.com", status=User.Status.PENDING)

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        Property.objects.create(
            estate=self.oak,
            plot_number="1",
            price=1000,
            required_deposit=100,
            allocated_to=self.jane,
            status=Property.Status.ALLOCATED,
        )

        # Signals may have queued notification emails; start clean.
        EmailOutbox.objects.all().delete()
        self.client.force_authenticate(self.admin)

    def url(self, newsletter, name):
        return reverse(f"newsletters:issue-{name}", args=[newsletter.pk])

    def draft(self, **fields):
        data = {"subject": "October news", "body_html": BODY, **fields}
        response = self.client.post(self.list_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        return Newsletter.objects.get(pk=response.json()["data"]["id"])

    def send(self, newsletter, **data):
        return self.client.post(self.url(newsletter, "send"), data, format="json")


class DraftTests(NewsletterTestCase):
    def test_admin_creates_sanitised_draft(self):
        response = self.client.post(
            self.list_url,
            {"subject": "Hi", "body_html": '<p onclick="x()">Hello</p><script>bad()</script>'},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S048")
        data = response.json()["data"]
        self.assertEqual(data["status"], "DRAFT")
        self.assertEqual(data["body_html"], "<p>Hello</p>")
        self.assertEqual(data["created_by_name"], "Ada")
        self.assertIsNone(data["stats"])

    def test_only_admins(self):
        for user in (self.manager, self.jane):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)
                self.assertEqual(
                    self.client.get(self.list_url).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_rejects_empty_content(self):
        response = self.client.post(
            self.list_url,
            {"subject": "Hi", "body_html": "<p>  </p><script>x</script>"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("body_html", response.json())

    def test_estate_audience_needs_an_estate(self):
        response = self.client.post(
            self.list_url,
            {"subject": "Hi", "body_html": BODY, "audience": "ESTATE_MEMBERS"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        newsletter = self.draft(audience="ALL_MEMBERS", estate=self.oak.pk)
        self.assertIsNone(newsletter.estate)

    def test_preview_counts_recipients(self):
        newsletter = self.draft()

        response = self.client.get(self.url(newsletter, "preview"))

        data = response.json()["data"]
        self.assertEqual(response.json()["code"], "S051")
        self.assertIn("October news", data["html"])
        self.assertIn("Oak Estates", data["html"])
        self.assertIn("Report (https://example.com/r)", data["text"])
        self.assertEqual(data["recipient_count"], 2)

    def test_test_send(self):
        newsletter = self.draft()

        response = self.client.post(self.url(newsletter, "test"), {}, format="json")

        self.assertEqual(response.json()["code"], "S052")
        email = EmailOutbox.objects.get()
        self.assertEqual(email.to_email, "admin@example.com")
        self.assertEqual(email.subject, "[Test] October news")
        self.assertEqual(email.priority, EmailOutbox.Priority.TRANSACTIONAL)
        self.assertFalse(Delivery.objects.exists())

        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, Newsletter.Status.DRAFT)


class SendTests(NewsletterTestCase):
    def test_send_now_queues_one_email_per_member(self):
        newsletter = self.draft()

        response = self.send(newsletter)

        self.assertEqual(response.json()["code"], "S053")
        self.assertEqual(response.json()["data"]["status"], "SENT")

        emails = EmailOutbox.objects.order_by("to_email")
        self.assertEqual(
            [e.to_email for e in emails],
            ["jane@example.com", "peter@example.com"],
        )

        jane_email = emails[0]
        token = Subscriber.objects.get(user=self.jane).token
        self.assertEqual(jane_email.priority, EmailOutbox.Priority.BULK)
        self.assertEqual(
            jane_email.headers,
            {
                "List-Unsubscribe": f"<https://api.example.com/api/v1/newsletters/unsubscribe/{token}/>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
        self.assertIn(f"/newsletter/unsubscribe?token={token}", jane_email.html_body)
        self.assertIn(f"/newsletter/unsubscribe?token={token}", jane_email.body)
        self.assertNotIn(str(token), emails[1].html_body)

    def test_cannot_send_twice_or_edit_after(self):
        newsletter = self.draft()
        self.send(newsletter)

        for response in (
            self.send(newsletter),
            self.client.patch(self.url(newsletter, "detail"), {"subject": "x"}, format="json"),
            self.client.delete(self.url(newsletter, "detail")),
        ):
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(response.json()["code"], "E012")

        self.assertEqual(EmailOutbox.objects.count(), 2)

    def test_estate_audience(self):
        newsletter = self.draft(audience="ESTATE_MEMBERS", estate=self.oak.pk)

        self.send(newsletter)

        self.assertEqual(
            list(EmailOutbox.objects.values_list("to_email", flat=True)),
            ["jane@example.com"],
        )

    def test_staff_audience(self):
        newsletter = self.draft(audience="STAFF")

        self.send(newsletter)

        self.assertEqual(
            set(EmailOutbox.objects.values_list("to_email", flat=True)),
            {"admin@example.com", "manager@example.com"},
        )

    def test_unsubscribed_members_are_skipped(self):
        Subscriber.objects.create(
            user=self.peter,
            email=self.peter.email,
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        newsletter = self.draft()

        self.send(newsletter)

        self.assertEqual(
            list(EmailOutbox.objects.values_list("to_email", flat=True)),
            ["jane@example.com"],
        )

    def test_subscriber_email_follows_account(self):
        Subscriber.objects.create(user=self.jane, email="old@example.com")
        newsletter = self.draft(audience="ESTATE_MEMBERS", estate=self.oak.pk)

        self.send(newsletter)

        self.assertEqual(EmailOutbox.objects.get().to_email, "jane@example.com")

    def test_no_recipients(self):
        empty = Estate.objects.create(name="Empty", code="empty", location="x")
        newsletter = self.draft(audience="ESTATE_MEMBERS", estate=empty.pk)

        response = self.send(newsletter)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "E013")
        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, Newsletter.Status.DRAFT)

    def test_worker_delivers_html_with_headers_and_stats(self):
        newsletter = self.draft()
        self.send(newsletter)

        send_pending_emails()

        self.assertEqual(len(mail.outbox), 2)
        message = mail.outbox[0]
        self.assertEqual(message.alternatives[0][1], "text/html")
        self.assertIn("List-Unsubscribe", message.extra_headers)

        response = self.client.get(self.url(newsletter, "detail"))
        self.assertEqual(
            response.json()["data"]["stats"],
            {"recipients": 2, "sent": 2, "pending": 0, "failed": 0},
        )

    def test_transactional_email_jumps_the_queue(self):
        newsletter = self.draft()
        self.send(newsletter)
        EmailOutbox.objects.create(
            to_email="urgent@example.com",
            subject="Account approved",
            body="Welcome",
        )

        send_pending_emails(batch_size=1)

        self.assertEqual(mail.outbox[0].to, ["urgent@example.com"])


class ScheduleTests(NewsletterTestCase):
    def test_schedule_and_cancel(self):
        newsletter = self.draft()
        send_at = timezone.now() + timedelta(days=1)

        response = self.send(newsletter, send_at=send_at.isoformat())
        self.assertEqual(response.json()["code"], "S054")
        self.assertEqual(response.json()["data"]["status"], "SCHEDULED")
        self.assertFalse(EmailOutbox.objects.exists())

        response = self.client.post(self.url(newsletter, "cancel"))
        self.assertEqual(response.json()["code"], "S055")
        self.assertEqual(response.json()["data"]["status"], "DRAFT")

    def test_past_time_rejected(self):
        newsletter = self.draft()

        response = self.send(newsletter, send_at=(timezone.now() - timedelta(minutes=1)).isoformat())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduled_newsletter_can_still_be_edited(self):
        newsletter = self.draft()
        self.send(newsletter, send_at=(timezone.now() + timedelta(days=1)).isoformat())

        response = self.client.patch(
            self.url(newsletter, "detail"),
            {"subject": "Updated"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_worker_releases_due_newsletters(self):
        newsletter = self.draft()
        later = self.draft(subject="Later")
        services.schedule(newsletter, timezone.now() + timedelta(minutes=5), self.admin)
        services.schedule(later, timezone.now() + timedelta(days=5), self.admin)
        Newsletter.objects.filter(pk=newsletter.pk).update(
            scheduled_for=timezone.now() - timedelta(seconds=1),
        )

        call_command("send_notifications", stdout=io.StringIO())

        newsletter.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(newsletter.status, Newsletter.Status.SENT)
        self.assertEqual(newsletter.sent_by, self.admin)
        self.assertEqual(later.status, Newsletter.Status.SCHEDULED)
        self.assertEqual(len(mail.outbox), 2)


# ============================================================================
# MEMBERS
# ============================================================================

class ArchiveTests(NewsletterTestCase):
    def setUp(self):
        super().setUp()
        self.everyone = self.draft(subject="For members")
        self.for_oak = self.draft(subject="For Oak", audience="ESTATE_MEMBERS", estate=self.oak.pk)
        self.for_staff = self.draft(subject="For staff", audience="STAFF")
        self.unsent = self.draft(subject="Draft")

        for newsletter in (self.everyone, self.for_oak, self.for_staff):
            services.send_now(newsletter, self.admin)

    def subjects(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(reverse("newsletters:archive-list"))
        self.assertEqual(response.json()["code"], "S056")
        return {n["subject"] for n in response.json()["data"]["results"]}

    def test_members_see_issues_meant_for_them(self):
        self.assertEqual(self.subjects(self.jane), {"For members", "For Oak"})
        self.assertEqual(self.subjects(self.peter), {"For members"})

    def test_staff_see_all_sent_issues(self):
        self.assertEqual(
            self.subjects(self.manager),
            {"For members", "For Oak", "For staff"},
        )

    def test_member_cannot_open_others(self):
        self.client.force_authenticate(self.peter)

        response = self.client.get(reverse("newsletters:archive-detail", args=[self.for_oak.pk]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SubscriptionTests(NewsletterTestCase):
    url = reverse("newsletters:subscription")

    def test_members_are_subscribed_by_default_and_can_opt_out(self):
        self.client.force_authenticate(self.jane)

        response = self.client.get(self.url)
        self.assertEqual(response.json()["data"], {"subscribed": True})

        response = self.client.patch(self.url, {"subscribed": False}, format="json")
        self.assertEqual(response.json()["code"], "S059")
        self.assertEqual(response.json()["data"], {"subscribed": False})

        subscriber = Subscriber.objects.get(user=self.jane)
        self.assertEqual(subscriber.status, Subscriber.Status.UNSUBSCRIBED)
        self.assertIsNotNone(subscriber.unsubscribed_at)


class UnsubscribeLinkTests(NewsletterTestCase):
    def setUp(self):
        super().setUp()
        self.subscriber = Subscriber.objects.create(user=self.jane, email=self.jane.email)
        self.link = reverse("newsletters:unsubscribe", args=[self.subscriber.token])
        self.client.force_authenticate(None)

    def test_status_hides_most_of_the_email(self):
        response = self.client.get(self.link)

        self.assertEqual(response.json()["data"], {"email": "ja***@example.com", "subscribed": True})

    def test_one_click_unsubscribe_from_mail_app(self):
        response = self.client.post(
            self.link,
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscriber.refresh_from_db()
        self.assertEqual(self.subscriber.status, Subscriber.Status.UNSUBSCRIBED)

    def test_undo(self):
        self.client.post(self.link, {"subscribed": False}, format="json")

        response = self.client.post(self.link, {"subscribed": True}, format="json")

        self.assertEqual(response.json()["data"]["subscribed"], True)

    def test_works_even_with_a_stale_login_token(self):
        response = self.client.post(
            self.link,
            {},
            format="json",
            HTTP_AUTHORIZATION="Bearer expired.or.garbage",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unknown_token(self):
        response = self.client.get(
            reverse("newsletters:unsubscribe", args=["00000000-0000-0000-0000-000000000000"])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
