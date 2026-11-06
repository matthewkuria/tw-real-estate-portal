from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.properties.models import Property

from .models import Estate


def make_user(email, role):
    return User.objects.create_user(
        email=email, password="Pass-word-123!", role=role, status=User.Status.ACTIVE, is_active=True
    )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EstateTests(APITestCase):
    list_url = reverse("estates:estate-list")

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.member = make_user("jane@example.com", User.Role.MEMBER)
        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.client.force_authenticate(self.admin)

    def detail(self, estate=None):
        return reverse("estates:estate-detail", args=[(estate or self.oak).pk])

    def test_staff_list_with_plot_counts_members_cant(self):
        Property.objects.create(estate=self.oak, plot_number="1")

        self.client.force_authenticate(self.manager)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"][0]["plot_count"], 1)

        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_adds_estate_with_code_made_from_name(self):
        Estate.objects.create(name="Palm Grove old", code="palm-grove", location="x")

        response = self.client.post(self.list_url, {"name": " Palm Grove ", "location": "Mombasa"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["name"], "Palm Grove")
        self.assertEqual(response.data["data"]["code"], "palm-grove-2")

    def test_names_and_codes_are_unique_ignoring_case(self):
        name = self.client.post(self.list_url, {"name": "OAK", "location": "x"}, format="json")
        code = self.client.post(self.list_url, {"name": "Elm", "code": "OAK", "location": "x"}, format="json")

        self.assertEqual(name.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", name.data)
        self.assertEqual(code.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", code.data)

    def test_admin_edits_and_deactivates(self):
        response = self.client.patch(self.detail(), {"location": "Karen", "is_active": False}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.oak.refresh_from_db()
        self.assertEqual(self.oak.location, "Karen")
        self.assertFalse(self.oak.is_active)

    def test_only_admins_change_estates(self):
        self.client.force_authenticate(self.manager)

        self.assertEqual(
            self.client.post(self.list_url, {"name": "Elm", "location": "x"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.patch(self.detail(), {"name": "x"}, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.delete(self.detail()).status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_estates_take_no_new_plots(self):
        self.oak.is_active = False
        self.oak.save()

        response = self.client.post(
            reverse("properties:plot-list"), {"estate": self.oak.pk, "plot_number": "7"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("estate", response.data)

    def test_delete_only_when_unused(self):
        Property.objects.create(estate=self.oak, plot_number="1")
        empty = Estate.objects.create(name="Empty", code="empty", location="x")

        refused = self.client.delete(self.detail())
        deleted = self.client.delete(self.detail(empty))

        self.assertEqual(refused.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(refused.data["code"], "E027")
        self.assertTrue(Estate.objects.filter(pk=self.oak.pk).exists())
        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.assertFalse(Estate.objects.filter(pk=empty.pk).exists())
