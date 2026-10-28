import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate
from apps.properties.models import Property

from .models import HousePhoto, RentalHouse, Tenant


MEDIA_ROOT = tempfile.mkdtemp(prefix="rentals-tests-")


def photo_file():
    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), "teal").save(buffer, format="JPEG")
    return SimpleUploadedFile("house.jpg", buffer.getvalue(), content_type="image/jpeg")


def make_user(email, role=User.Role.MEMBER, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=User.Status.ACTIVE,
        is_active=True,
        **extra,
    )


@override_settings(
    MEDIA_ROOT=MEDIA_ROOT,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class RentalTestCase(APITestCase):
    houses_url = reverse("rentals:house-list")
    tenants_url = reverse("rentals:tenant-list")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.landlord = make_user("landlord@example.com", first_name="Grace", last_name="Njeri")

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")

        self.plot = Property.objects.create(
            estate=self.oak,
            plot_number="14",
            price=1000,
            required_deposit=100,
            allocated_to=self.landlord,
            status=Property.Status.ALLOCATED,
        )
        self.palm_plot = Property.objects.create(
            estate=self.palm,
            plot_number="3",
            price=1000,
            required_deposit=100,
        )

        self.client.force_authenticate(self.manager)

    def house(self, number="A1", plot=None, **fields):
        return RentalHouse.objects.create(
            property=plot or self.plot,
            house_number=number,
            house_type=fields.pop("house_type", RentalHouse.HouseType.BEDSITTER),
            **fields,
        )

    def tenant(self, house, name="Kevin Omondi", **fields):
        return Tenant.objects.create(
            house=house,
            full_name=name,
            phone=fields.pop("phone", "+254711000222"),
            **fields,
        )

    def house_url(self, house, name="detail", **kwargs):
        return reverse(f"rentals:house-{name}", kwargs={"pk": house.pk, **kwargs})

    def tenant_url(self, tenant):
        return reverse("rentals:tenant-detail", args=[tenant.pk])


# ============================================================================
# ACCESS
# ============================================================================

class AccessTests(RentalTestCase):
    def test_staff_can_read(self):
        for user in (self.admin, self.manager, self.finance):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)

                self.assertEqual(self.client.get(self.houses_url).status_code, status.HTTP_200_OK)
                self.assertEqual(self.client.get(self.tenants_url).status_code, status.HTTP_200_OK)

    def test_finance_cannot_change(self):
        self.client.force_authenticate(self.finance)

        response = self.client.post(
            self.houses_url,
            {"property": self.plot.pk, "house_number": "A1", "house_type": "BEDSITTER"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_residents_and_landlords_have_no_access(self):
        house = self.house()
        self.client.force_authenticate(self.landlord)

        for url in (self.houses_url, self.house_url(house), self.tenants_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.houses_url).status_code, status.HTTP_401_UNAUTHORIZED)


# ============================================================================
# HOUSES
# ============================================================================

class HouseTests(RentalTestCase):
    def test_add_house(self):
        response = self.client.post(
            self.houses_url,
            {
                "property": self.plot.pk,
                "house_number": " A3 ",
                "house_type": "MAISONETTE",
                "bedrooms": 3,
                "description": "Corner unit with garden",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        body = response.json()
        self.assertEqual(body["code"], "S075")

        data = body["data"]
        self.assertEqual(data["house_number"], "A3")
        self.assertEqual(data["house_type_label"], "Maisonette")
        self.assertEqual(data["plot_number"], "14")
        self.assertEqual(data["estate_name"], "Oak")
        self.assertEqual(
            data["landlord"],
            {"id": self.landlord.pk, "name": "Grace Njeri", "email": "landlord@example.com"},
        )
        self.assertEqual(data["tenant_count"], 0)
        self.assertEqual((data["tenants"], data["photos"]), ([], []))

    def test_unallocated_plot_has_no_landlord(self):
        house = self.house(plot=self.palm_plot)

        data = self.client.get(self.house_url(house)).json()["data"]

        self.assertIsNone(data["landlord"])

    def test_house_number_unique_per_plot(self):
        self.house("A1")

        response = self.client.post(
            self.houses_url,
            {"property": self.plot.pk, "house_number": "a1", "house_type": "BEDSITTER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("house_number", response.json())

        response = self.client.post(
            self.houses_url,
            {"property": self.palm_plot.pk, "house_number": "A1", "house_type": "BEDSITTER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_detail_lists_tenants(self):
        house = self.house()
        self.tenant(house, "Kevin Omondi", user=make_user("kevin@example.com"))
        self.tenant(house, "Amina Hassan")

        data = self.client.get(self.house_url(house)).json()["data"]

        self.assertEqual(data["tenant_count"], 2)
        self.assertEqual(
            [(t["full_name"], t["has_account"]) for t in data["tenants"]],
            [("Amina Hassan", False), ("Kevin Omondi", True)],
        )

    def test_edit(self):
        house = self.house()

        response = self.client.patch(self.house_url(house), {"bedrooms": 0}, format="json")

        self.assertEqual(response.json()["code"], "S076")
        self.assertEqual(response.json()["data"]["bedrooms"], 0)

    def test_cannot_remove_house_with_tenants(self):
        house = self.house()
        tenant = self.tenant(house)

        response = self.client.delete(self.house_url(house))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "E018")

        tenant.delete()
        response = self.client.delete(self.house_url(house))
        self.assertEqual(response.json()["code"], "S077")
        self.assertFalse(RentalHouse.objects.exists())


class HouseListTests(RentalTestCase):
    def setUp(self):
        super().setUp()
        self.a1 = self.house("A1", bedrooms=1)
        self.a2 = self.house("A2", house_type=RentalHouse.HouseType.BUNGALOW, bedrooms=3)
        self.p1 = self.house("P1", plot=self.palm_plot)
        self.tenant(self.a1, "Kevin Omondi", phone="0722111333")

    def numbers(self, **params):
        response = self.client.get(self.houses_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.json()["code"], "S073")
        return [h["house_number"] for h in response.json()["data"]["results"]]

    def test_ordered_by_estate_plot_and_number(self):
        self.assertEqual(self.numbers(), ["A1", "A2", "P1"])

    def test_filters(self):
        self.assertEqual(self.numbers(estate=self.palm.pk), ["P1"])
        self.assertEqual(self.numbers(property=self.plot.pk), ["A1", "A2"])
        self.assertEqual(self.numbers(landlord=self.landlord.pk), ["A1", "A2"])
        self.assertEqual(self.numbers(house_type="BUNGALOW"), ["A2"])
        self.assertEqual(self.numbers(bedrooms=1), ["A1"])

    def test_occupancy(self):
        self.tenant(self.a1, "Second Tenant")

        self.assertEqual(self.numbers(occupied="true"), ["A1"])
        self.assertEqual(self.numbers(occupied="false"), ["A2", "P1"])

    def test_search(self):
        cases = {
            "a2": ["A2"],
            "14": ["A1", "A2"],
            "palm": ["P1"],
            "njeri": ["A1", "A2"],
            "kevin": ["A1"],
            "0722111333": ["A1"],
        }

        for search, expected in cases.items():
            with self.subTest(search=search):
                self.assertEqual(self.numbers(search=search), expected)

    def test_invalid_filters(self):
        for params in ({"estate": "x"}, {"house_type": "CASTLE"}, {"occupied": "maybe"}):
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get(self.houses_url, params).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )


class PhotoTests(RentalTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.house()

    def add_photo(self, **data):
        return self.client.post(
            self.house_url(self.target, "add-photo"),
            {"image": photo_file(), **data},
            format="multipart",
        )

    def test_add_photo(self):
        response = self.add_photo(caption="Front view")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        self.assertEqual(response.json()["code"], "S078")

        photo = response.json()["data"]["photos"][0]
        self.assertEqual(photo["caption"], "Front view")
        self.assertTrue(photo["image"].startswith("http://testserver/media/rentals/photos/"))
        self.assertTrue(photo["thumbnail"].endswith(".webp"))

        listed = self.client.get(self.houses_url).json()["data"]["results"][0]
        self.assertTrue(listed["cover_photo"].endswith(".webp"))

    def test_bad_photo_rejected(self):
        response = self.client.post(
            self.house_url(self.target, "add-photo"),
            {"image": SimpleUploadedFile("x.jpg", b"nope", content_type="image/jpeg")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_photo_deletes_files(self):
        self.add_photo()
        photo = HousePhoto.objects.get()
        image, thumb = photo.image, photo.thumbnail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                self.house_url(self.target, "remove-photo", photo_id=photo.pk)
            )

        self.assertEqual(response.json()["code"], "S079")
        self.assertEqual(response.json()["data"]["photos"], [])
        self.assertFalse(image.storage.exists(image.name))
        self.assertFalse(thumb.storage.exists(thumb.name))

    def test_photo_must_belong_to_house(self):
        self.add_photo()
        photo = HousePhoto.objects.get()
        other = self.house("B1")

        response = self.client.delete(self.house_url(other, "remove-photo", photo_id=photo.pk))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(HousePhoto.objects.exists())


# ============================================================================
# TENANTS
# ============================================================================

class TenantTests(RentalTestCase):
    def setUp(self):
        super().setUp()
        self.a1 = self.house("A1")
        self.a2 = self.house("A2")

    def test_add_tenant(self):
        response = self.client.post(
            self.tenants_url,
            {
                "house": self.a1.pk,
                "full_name": "Kevin Omondi",
                "phone": "+254711000222",
                "email": "kevin@example.com",
                "id_number": "12345678",
                "move_in_date": "2026-09-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        data = response.json()["data"]
        self.assertEqual(response.json()["code"], "S082")
        self.assertEqual(
            (data["house_number"], data["plot_number"], data["estate_name"]),
            ("A1", "14", "Oak"),
        )
        self.assertIsNone(data["account"])

    def test_required_fields(self):
        response = self.client.post(self.tenants_url, {}, format="json")

        self.assertEqual(set(response.json()), {"house", "full_name", "phone"})

    def test_link_account(self):
        account = make_user("kevin@example.com", first_name="Kevin")
        tenant = self.tenant(self.a1)

        response = self.client.patch(self.tenant_url(tenant), {"user": account.pk}, format="json")

        self.assertEqual(response.json()["code"], "S083")
        self.assertEqual(response.json()["data"]["account"]["email"], "kevin@example.com")

        other = self.tenant(self.a2, "Someone Else")
        response = self.client.patch(self.tenant_url(other), {"user": account.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.json())

    def test_move_to_another_house(self):
        tenant = self.tenant(self.a1)

        response = self.client.patch(self.tenant_url(tenant), {"house": self.a2.pk}, format="json")

        self.assertEqual(response.json()["data"]["house_number"], "A2")

    def test_remove_tenant(self):
        tenant = self.tenant(self.a1)

        response = self.client.delete(self.tenant_url(tenant))

        self.assertEqual(response.json()["code"], "S084")
        self.assertFalse(Tenant.objects.exists())

    def test_list_filters_and_search(self):
        self.tenant(self.a1, "Kevin Omondi", id_number="12345678", user=make_user("k@example.com"))
        self.tenant(self.a2, "Amina Hassan", phone="0733999888", email="amina@example.com")
        palm_house = self.house("P1", plot=self.palm_plot)
        self.tenant(palm_house, "Brian Kip")

        def names(**params):
            response = self.client.get(self.tenants_url, params)
            self.assertEqual(response.json()["code"], "S080")
            return [t["full_name"] for t in response.json()["data"]["results"]]

        self.assertEqual(names(), ["Amina Hassan", "Brian Kip", "Kevin Omondi"])
        self.assertEqual(names(estate=self.palm.pk), ["Brian Kip"])
        self.assertEqual(names(house=self.a2.pk), ["Amina Hassan"])
        self.assertEqual(names(has_account="true"), ["Kevin Omondi"])
        self.assertEqual(names(has_account="false"), ["Amina Hassan", "Brian Kip"])
        self.assertEqual(names(search="0733999888"), ["Amina Hassan"])
        self.assertEqual(names(search="12345678"), ["Kevin Omondi"])
        self.assertEqual(names(search="amina@"), ["Amina Hassan"])
