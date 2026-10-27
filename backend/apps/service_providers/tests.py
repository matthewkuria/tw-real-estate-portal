from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate

from .models import ServiceProvider, StatusChange


def make_user(email, role):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=User.Status.ACTIVE,
        is_active=True,
        first_name=email.split("@")[0].title(),
    )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ProviderTestCase(APITestCase):
    list_url = reverse("service_providers:provider-list")

    def setUp(self):
        self.super_admin = make_user("super@example.com", User.Role.SUPER_ADMIN)
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.member = make_user("member@example.com", User.Role.MEMBER)

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")

        self.client.force_authenticate(self.manager)

    def url(self, provider, name="detail"):
        return reverse(f"service_providers:provider-{name}", args=[provider.pk])

    def provider(self, **fields):
        defaults = {
            "name": "Flow Plumbers",
            "category": ServiceProvider.Category.PLUMBING,
            "phone": "+254712345678",
        }
        return ServiceProvider.objects.create(**{**defaults, **fields})

    def add(self, **data):
        payload = {
            "name": "Bright Electrical",
            "category": "ELECTRICAL",
            "phone": "+254700111222",
            **data,
        }
        return self.client.post(self.list_url, payload, format="json")

    def blacklist(self, provider, reason="Abandoned the job halfway"):
        return self.client.post(self.url(provider, "blacklist"), {"reason": reason}, format="json")


# ============================================================================
# ACCESS
# ============================================================================

class AccessTests(ProviderTestCase):
    def test_admins_and_managers_can_add(self):
        for user in (self.super_admin, self.admin, self.manager):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)

                response = self.add(name=f"Provider {user.pk}", phone=f"+25470000{user.pk:04d}")

                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_finance_can_read_but_not_change(self):
        provider = self.provider()
        self.client.force_authenticate(self.finance)

        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.add().status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.blacklist(provider).status_code, status.HTTP_403_FORBIDDEN)

    def test_residents_can_read_but_not_change(self):
        provider = self.provider()
        self.client.force_authenticate(self.member)

        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.add().status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.patch(self.url(provider), {"name": "x"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.blacklist(provider).status_code, status.HTTP_403_FORBIDDEN)

    def test_sign_in_required(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_providers_cannot_be_deleted(self):
        provider = self.provider()
        self.client.force_authenticate(self.super_admin)

        response = self.client.delete(self.url(provider))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(ServiceProvider.objects.filter(pk=provider.pk).exists())


# ============================================================================
# ADDING AND EDITING
# ============================================================================

class AddTests(ProviderTestCase):
    def test_add(self):
        response = self.add(
            contact_person="Ann Wanjiru",
            email="info@bright.co.ke",
            kra_pin=" p051234567x ",
            estates=[self.oak.pk],
        )

        body = response.json()
        self.assertEqual(body["code"], "S069")

        data = body["data"]
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["category_label"], "Electrical")
        self.assertEqual(data["kra_pin"], "P051234567X")
        self.assertEqual(data["estate_names"], ["Oak"])
        self.assertEqual(data["created_by_name"], "Manager")
        self.assertEqual(data["history"], [])

    def test_required_fields(self):
        response = self.client.post(self.list_url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.json()), {"name", "category", "phone"})

    def test_kra_pin_is_unique(self):
        self.provider(kra_pin="P051234567X")

        response = self.add(kra_pin="p051234567x")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("kra_pin", response.json())

    def test_status_cannot_be_set_directly(self):
        response = self.add(status="BLACKLISTED")

        self.assertEqual(response.json()["data"]["status"], "ACTIVE")

    def test_edit(self):
        provider = self.provider()

        response = self.client.patch(
            self.url(provider),
            {"notes": "Reliable, quick to respond", "estates": [self.palm.pk]},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S070")
        self.assertEqual(response.json()["data"]["estate_names"], ["Palm"])


class BlacklistedMatchTests(ProviderTestCase):
    def setUp(self):
        super().setUp()
        self.banned = self.provider(
            name="Shady Builders",
            phone="+254 722 000 111",
            email="boss@shady.co.ke",
            kra_pin="A000111222Z",
            status=ServiceProvider.Status.BLACKLISTED,
        )

    def test_matching_details_are_flagged(self):
        cases = {
            "phone": {"phone": "0722-000-111"},
            "email": {"email": "BOSS@shady.co.ke"},
        }

        for field, data in cases.items():
            with self.subTest(field=field):
                response = self.add(name="Totally New Builders", **data)

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                body = response.json()
                self.assertEqual(body["code"], "E017")
                self.assertFalse(body["success"])
                self.assertEqual(
                    body["data"]["blacklisted_matches"],
                    [{"id": self.banned.pk, "name": "Shady Builders", "matched_on": [field]}],
                )

        self.assertEqual(ServiceProvider.objects.count(), 1)

    def test_blacklisted_kra_pin_must_be_reinstated_not_re_added(self):
        for allow in (False, True):
            with self.subTest(allow_blacklisted_match=allow):
                response = self.add(kra_pin="a000111222z", allow_blacklisted_match=allow)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("Reinstate", response.json()["kra_pin"][0])

    def test_can_save_anyway_when_confirmed(self):
        response = self.add(phone="0722000111", allow_blacklisted_match=True)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_edits_that_dont_touch_contact_details_are_not_flagged(self):
        other = self.provider(name="Fine Co", phone="0722000111")

        response = self.client.patch(self.url(other), {"notes": "ok"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_editing_into_a_match_is_flagged(self):
        other = self.provider(name="Fine Co", phone="0700999888")

        response = self.client.patch(self.url(other), {"email": "boss@shady.co.ke"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_blacklisted_provider_can_be_edited_itself(self):
        response = self.client.patch(self.url(self.banned), {"phone": "0722000111"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ============================================================================
# BLACKLISTING
# ============================================================================

class BlacklistTests(ProviderTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.provider()

    def test_blacklist_records_who_and_why(self):
        response = self.blacklist(self.target)

        self.assertEqual(response.json()["code"], "S071")
        data = response.json()["data"]
        self.assertEqual(data["status"], "BLACKLISTED")
        self.assertEqual(data["blacklist_reason"], "Abandoned the job halfway")
        self.assertEqual(data["blacklisted_by_name"], "Manager")
        self.assertIsNotNone(data["blacklisted_at"])
        self.assertEqual(
            [(h["action"], h["changed_by_name"]) for h in data["history"]],
            [("BLACKLISTED", "Manager")],
        )

    def test_reason_is_required(self):
        for body in ({}, {"reason": ""}, {"reason": "bad"}):
            with self.subTest(body=body):
                response = self.client.post(self.url(self.target, "blacklist"), body, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.target.refresh_from_db()
        self.assertEqual(self.target.status, ServiceProvider.Status.ACTIVE)

    def test_cannot_blacklist_twice(self):
        self.blacklist(self.target)

        response = self.blacklist(self.target)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "E015")
        self.assertEqual(StatusChange.objects.count(), 1)

    def test_reinstate_keeps_history(self):
        self.blacklist(self.target)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url(self.target, "reinstate"),
            {"reason": "Finished the job and paid damages"},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S072")
        data = response.json()["data"]
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["blacklist_reason"], "")
        self.assertIsNone(data["blacklisted_by_name"])
        self.assertEqual(
            [(h["action"], h["changed_by_name"]) for h in data["history"]],
            [("REINSTATED", "Admin"), ("BLACKLISTED", "Manager")],
        )

    def test_cannot_reinstate_an_active_provider(self):
        response = self.client.post(
            self.url(self.target, "reinstate"),
            {"reason": "No reason at all"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "E016")


# ============================================================================
# LISTING
# ============================================================================

class ListTests(ProviderTestCase):
    def setUp(self):
        super().setUp()
        self.plumber = self.provider(name="Flow Plumbers", contact_person="Joe Otieno")
        self.guards = self.provider(
            name="Safe Guards",
            category=ServiceProvider.Category.SECURITY,
            phone="0733444555",
            email="ops@safeguards.co.ke",
            kra_pin="P099887766A",
        )
        self.guards.estates.add(self.oak)
        self.cleaners = self.provider(
            name="Clean Co",
            category=ServiceProvider.Category.CLEANING,
            phone="0711000999",
            status=ServiceProvider.Status.BLACKLISTED,
        )
        self.cleaners.estates.add(self.palm)

    def names(self, **params):
        response = self.client.get(self.list_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S067")
        return [p["name"] for p in response.json()["data"]["results"]]

    def test_alphabetical(self):
        self.assertEqual(self.names(), ["Clean Co", "Flow Plumbers", "Safe Guards"])

    def test_filters(self):
        self.assertEqual(self.names(status="BLACKLISTED"), ["Clean Co"])
        self.assertEqual(self.names(status="ACTIVE"), ["Flow Plumbers", "Safe Guards"])
        self.assertEqual(self.names(category="SECURITY"), ["Safe Guards"])

        # Providers with no estates work anywhere, so they're included.
        self.assertEqual(self.names(estate=self.oak.pk), ["Flow Plumbers", "Safe Guards"])

    def test_search(self):
        cases = {
            "plumb": ["Flow Plumbers"],
            "otieno": ["Flow Plumbers"],
            "safeguards.co": ["Safe Guards"],
            "p099887766a": ["Safe Guards"],
            "+254 733 444 555": ["Safe Guards"],
            "0733444555": ["Safe Guards"],
        }

        for search, expected in cases.items():
            with self.subTest(search=search):
                self.assertEqual(self.names(search=search), expected)

    def test_invalid_filters(self):
        for params in ({"status": "x"}, {"category": "x"}, {"estate": "x"}):
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get(self.list_url, params).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )


# ============================================================================
# RESIDENT VIEW
# ============================================================================

class ResidentViewTests(ProviderTestCase):
    def setUp(self):
        from apps.properties.models import Property

        super().setUp()
        Property.objects.create(
            estate=self.oak,
            plot_number="1",
            price=1000,
            required_deposit=100,
            allocated_to=self.member,
            status=Property.Status.ALLOCATED,
        )

        self.anywhere = self.provider(
            name="Anywhere Plumbers",
            kra_pin="P011111111A",
            notes="Overcharged once, watch the invoice",
        )
        self.oak_guards = self.provider(
            name="Oak Guards",
            category=ServiceProvider.Category.SECURITY,
            phone="0733444555",
        )
        self.oak_guards.estates.add(self.oak)
        self.palm_cleaners = self.provider(name="Palm Cleaners", phone="0744000111")
        self.palm_cleaners.estates.add(self.palm)
        self.banned = self.provider(
            name="Banned Builders",
            phone="0755000222",
            status=ServiceProvider.Status.BLACKLISTED,
            blacklist_reason="Fraud",
        )

        self.client.force_authenticate(self.member)

    def names(self, **params):
        response = self.client.get(self.list_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [p["name"] for p in response.json()["data"]["results"]]

    def test_sees_active_providers_for_their_estates(self):
        self.assertEqual(self.names(), ["Anywhere Plumbers", "Oak Guards"])

    def test_member_without_plot_sees_providers_that_work_anywhere(self):
        self.client.force_authenticate(make_user("new@example.com", User.Role.MEMBER))

        self.assertEqual(self.names(), ["Anywhere Plumbers"])

    def test_blacklisted_providers_stay_hidden(self):
        self.assertEqual(self.names(status="BLACKLISTED"), ["Anywhere Plumbers", "Oak Guards"])
        self.assertEqual(self.names(search="banned"), [])
        self.assertEqual(
            self.client.get(self.url(self.banned)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.get(self.url(self.palm_cleaners)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_only_contact_details_are_shown(self):
        expected = {
            "id", "name", "category", "category_label", "description",
            "contact_person", "phone", "email", "address", "website",
            "estate_names",
        }

        listed = self.client.get(self.list_url).json()["data"]["results"][0]
        detail = self.client.get(self.url(self.anywhere)).json()["data"]

        self.assertEqual(set(listed), expected)
        self.assertEqual(set(detail), expected)
        self.assertNotIn("Overcharged", str(detail))

    def test_search_works_without_kra_pins(self):
        self.assertEqual(self.names(search="0733444555"), ["Oak Guards"])
        self.assertEqual(self.names(category="SECURITY"), ["Oak Guards"])
        self.assertEqual(self.names(search="P011111111A"), [])

    def test_staff_still_see_everything(self):
        self.client.force_authenticate(self.finance)

        self.assertEqual(
            self.names(),
            ["Anywhere Plumbers", "Banned Builders", "Oak Guards", "Palm Cleaners"],
        )
        detail = self.client.get(self.url(self.anywhere)).json()["data"]
        self.assertEqual(detail["kra_pin"], "P011111111A")
        self.assertIn("history", detail)
