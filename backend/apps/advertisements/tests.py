import io
import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate
from apps.properties.models import Property

from .models import Advertisement


MEDIA_ROOT = tempfile.mkdtemp(prefix="ads-tests-")


def image_file(name="ad.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 800), "orange").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def make_user(email, role=User.Role.MEMBER, status=User.Status.ACTIVE):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=status,
        is_active=status == User.Status.ACTIVE,
    )


@override_settings(
    MEDIA_ROOT=MEDIA_ROOT,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AdTestCase(APITestCase):
    list_url = reverse("advertisements:ad-list")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.resident = make_user("resident@example.com")
        self.outsider = make_user("outsider@example.com")

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")
        Property.objects.create(
            estate=self.oak,
            plot_number="1",
            price=1000,
            required_deposit=100,
            allocated_to=self.resident,
            status=Property.Status.ALLOCATED,
        )

    def detail_url(self, ad):
        return reverse("advertisements:ad-detail", args=[ad.pk])

    def post(self, data, format="json", user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.post(self.list_url, data, format=format)

    def ad(self, **fields):
        defaults = {"category": Advertisement.Category.ITEM, "title": "Lawn mower"}
        return Advertisement.objects.create(**{**defaults, **fields})

    def titles(self, user=None, **params):
        self.client.force_authenticate(user)
        response = self.client.get(self.list_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return [ad["title"] for ad in response.json()["data"]["results"]]


# ============================================================================
# POSTING
# ============================================================================

class PostingTests(AdTestCase):
    def test_admin_posts_an_item(self):
        response = self.post(
            {
                "category": "ITEM",
                "title": "Lawn mower",
                "summary": "Barely used",
                "price": "15000.00",
                "contact_phone": "+254700000000",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        body = response.json()
        self.assertEqual(body["code"], "S064")
        self.assertEqual(body["data"]["category_label"], "Item for sale")
        self.assertEqual(body["data"]["state"], "LIVE")
        self.assertEqual(body["data"]["visibility"], "PUBLIC")
        self.assertEqual(Advertisement.objects.get().created_by, self.admin)

    def test_only_admins_can_post(self):
        for user in (self.manager, self.resident):
            with self.subTest(role=user.role):
                response = self.post({"category": "ITEM", "title": "x"}, user=user)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(None)
        response = self.client.post(self.list_url, {"category": "ITEM", "title": "x"}, format="json")
        self.assertIn(response.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})
        self.assertFalse(Advertisement.objects.exists())

    def test_upload_with_image_by_form(self):
        response = self.post(
            {"category": "SERVICE", "title": "Plumber", "image": image_file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        data = response.json()["data"]
        self.assertTrue(data["image"].startswith("http://testserver/media/ads/images/"))
        self.assertTrue(data["thumbnail"].endswith(".webp"))
        self.assertEqual((data["image_width"], data["image_height"]), (1200, 800))

        # Booleans left out of form data keep their defaults.
        self.assertTrue(data["is_published"])
        self.assertFalse(data["is_featured"])

    def test_bad_image_rejected(self):
        response = self.post(
            {
                "category": "ITEM",
                "title": "x",
                "image": SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image", response.json())

    def test_event_rules(self):
        starts = timezone.now() + timedelta(days=7)

        response = self.post({"category": "EVENT", "title": "Open day"})
        self.assertIn("event_starts_at", response.json())

        response = self.post({
            "category": "EVENT",
            "title": "Open day",
            "event_starts_at": starts.isoformat(),
            "event_ends_at": (starts - timedelta(hours=1)).isoformat(),
        })
        self.assertIn("event_ends_at", response.json())

    def test_event_ad_expires_when_event_ends(self):
        starts = timezone.now() + timedelta(days=7)
        ends = starts + timedelta(hours=4)

        response = self.post({
            "category": "EVENT",
            "title": "Open day",
            "location": "Clubhouse",
            "event_starts_at": starts.isoformat(),
            "event_ends_at": ends.isoformat(),
        })

        ad = Advertisement.objects.get(pk=response.json()["data"]["id"])
        self.assertEqual(ad.expires_at, ends)

        response = self.post({
            "category": "EVENT",
            "title": "Talk",
            "event_starts_at": starts.isoformat(),
        })

        ad = Advertisement.objects.get(pk=response.json()["data"]["id"])
        self.assertEqual(ad.expires_at, starts)

    def test_other_validation(self):
        now = timezone.now()

        cases = {
            "link_url": {"link_label": "Buy now"},
            "expires_at": {"publish_at": now.isoformat(), "expires_at": (now - timedelta(days=1)).isoformat()},
            "price": {"price": "-5"},
            "category": {"category": "CAR"},
        }

        for field, extra in cases.items():
            with self.subTest(field=field):
                response = self.post({"category": "ITEM", "title": "x", **extra})

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.json())


# ============================================================================
# READING
# ============================================================================

class VisibilityTests(AdTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()

        self.ad(title="Public")
        self.ad(title="Members", visibility="MEMBERS")
        self.ad(title="Oak members", visibility="MEMBERS", estate=self.oak)
        self.ad(title="Palm members", visibility="MEMBERS", estate=self.palm)
        self.ad(title="Draft", is_published=False)
        self.ad(title="Scheduled", publish_at=now + timedelta(days=1))
        self.ad(title="Expired", publish_at=now - timedelta(days=2), expires_at=now - timedelta(days=1))

    def test_website_visitors_see_live_public_ads(self):
        self.assertEqual(self.titles(), ["Public"])

    def test_members_see_ads_for_them(self):
        self.assertEqual(
            set(self.titles(self.resident)),
            {"Public", "Members", "Oak members"},
        )
        self.assertEqual(set(self.titles(self.outsider)), {"Public", "Members"})

    def test_staff_see_every_members_ad(self):
        self.assertEqual(
            set(self.titles(self.manager)),
            {"Public", "Members", "Oak members", "Palm members"},
        )

    def test_inactive_accounts_get_the_public_view(self):
        pending = make_user("pending@example.com", status=User.Status.PENDING)

        self.assertEqual(self.titles(pending), ["Public"])

    def test_hidden_ads_return_404(self):
        draft = Advertisement.objects.get(title="Draft")
        palm = Advertisement.objects.get(title="Palm members")

        for user, ad in ((None, draft), (self.resident, palm)):
            with self.subTest(ad=ad.title):
                self.client.force_authenticate(user)
                response = self.client.get(self.detail_url(ad))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admins_see_everything_and_filter_by_state(self):
        self.assertEqual(len(self.titles(self.admin)), 7)

        for state, expected in {
            "DRAFT": ["Draft"],
            "SCHEDULED": ["Scheduled"],
            "EXPIRED": ["Expired"],
        }.items():
            with self.subTest(state=state):
                self.assertEqual(self.titles(self.admin, state=state), expected)

        self.assertEqual(
            set(self.titles(self.admin, state="LIVE")),
            {"Public", "Members", "Oak members", "Palm members"},
        )


class FilterTests(AdTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()

        self.ad(title="Mower", category="ITEM", publish_at=now - timedelta(hours=3))
        self.ad(title="Plumbing", category="SERVICE", description="Leaks fixed fast",
                publish_at=now - timedelta(hours=2))
        self.ad(title="Featured sofa", category="ITEM", is_featured=True,
                publish_at=now - timedelta(hours=5))
        self.ad(title="Later event", category="EVENT", event_starts_at=now + timedelta(days=9))
        self.ad(title="Soon event", category="EVENT", event_starts_at=now + timedelta(days=2))
        self.ad(title="Running event", category="EVENT", event_starts_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=2))

    def test_featured_first_then_newest(self):
        titles = self.titles()

        self.assertEqual(titles[0], "Featured sofa")
        self.assertLess(titles.index("Plumbing"), titles.index("Mower"))

    def test_filters(self):
        self.assertEqual(set(self.titles(category="ITEM")), {"Mower", "Featured sofa"})
        self.assertEqual(self.titles(featured="true"), ["Featured sofa"])
        self.assertEqual(self.titles(search="leaks"), ["Plumbing"])
        self.assertEqual(
            self.titles(upcoming="true", ordering="event_date"),
            ["Soon event", "Later event"],
        )

    def test_estate_filter(self):
        self.ad(title="Oak sale", estate=self.oak)

        self.assertEqual(self.titles(estate=self.oak.pk), ["Oak sale"])

    def test_invalid_params(self):
        for params in ({"category": "CAR"}, {"estate": "x"}, {"ordering": "x"}):
            with self.subTest(params=params):
                self.client.force_authenticate(None)
                response = self.client.get(self.list_url, params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(self.admin)
        response = self.client.get(self.list_url, {"state": "x"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_state_filter_is_ignored_for_the_public(self):
        self.assertEqual(len(self.titles(state="DRAFT")), 6)


# ============================================================================
# MANAGING
# ============================================================================

class ManageTests(AdTestCase):
    def setUp(self):
        super().setUp()
        response = self.post(
            {"category": "SERVICE", "title": "Plumber", "image": image_file()},
            format="multipart",
        )
        self.ad_obj = Advertisement.objects.get(pk=response.json()["data"]["id"])

    def exists(self, field):
        return bool(field.name) and field.storage.exists(field.name)

    def test_edit(self):
        response = self.client.patch(
            self.detail_url(self.ad_obj),
            {"is_featured": True, "summary": "24/7"},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S065")
        self.ad_obj.refresh_from_db()
        self.assertTrue(self.ad_obj.is_featured)
        self.assertTrue(self.exists(self.ad_obj.image))

    def test_remove_image(self):
        old_image, old_thumb = self.ad_obj.image, self.ad_obj.thumbnail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                self.detail_url(self.ad_obj),
                {"remove_image": True},
                format="json",
            )

        self.assertIsNone(response.json()["data"]["image"])
        self.assertFalse(self.exists(old_image))
        self.assertFalse(self.exists(old_thumb))

    def test_delete_removes_files(self):
        image, thumb = self.ad_obj.image, self.ad_obj.thumbnail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.detail_url(self.ad_obj))

        self.assertEqual(response.json()["code"], "S066")
        self.assertFalse(Advertisement.objects.exists())
        self.assertFalse(self.exists(image))
        self.assertFalse(self.exists(thumb))

    def test_managers_cannot_edit(self):
        self.client.force_authenticate(self.manager)

        response = self.client.patch(self.detail_url(self.ad_obj), {"title": "x"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
