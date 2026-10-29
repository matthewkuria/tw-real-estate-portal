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

from .models import ListingPhoto, PropertyListing


MEDIA_ROOT = tempfile.mkdtemp(prefix="listings-tests-")


def photo_file(name="house.jpg", colour="teal"):
    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), colour).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def make_user(email, role=User.Role.MEMBER):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=User.Status.ACTIVE,
        is_active=True,
    )


@override_settings(
    MEDIA_ROOT=MEDIA_ROOT,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class ListingTestCase(APITestCase):
    list_url = reverse("listings:listing-list")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.member = make_user("member@example.com")

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")
        self.plot = Property.objects.create(
            estate=self.oak,
            plot_number="14",
            price=1000,
            required_deposit=100,
        )

    def listing(self, title="Oak villa", **fields):
        defaults = {
            "listing_type": PropertyListing.ListingType.SALE,
            "property_type": PropertyListing.PropertyType.HOUSE,
            "price": 5_000_000,
            "location": "Ruiru",
        }
        return PropertyListing.objects.create(title=title, **{**defaults, **fields})

    def detail_url(self, listing):
        return reverse("listings:listing-detail", args=[listing.pk])

    def photos_url(self, listing):
        return reverse("listings:listing-add-photos", args=[listing.pk])

    def photo_url(self, listing, photo):
        return reverse("listings:listing-update-photo", args=[listing.pk, photo.pk])

    def results(self, response):
        return [item["title"] for item in response.data["data"]["results"]]


class CreateListingTests(ListingTestCase):
    def test_admin_lists_property_with_images(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.list_url,
            {
                "title": "3 bedroom bungalow",
                "listing_type": "SALE",
                "property_type": "HOUSE",
                "price": "7500000",
                "location": "Ruiru",
                "bedrooms": 3,
                "features": ["Borehole", "Gated", "borehole "],
                "images": [photo_file("a.jpg"), photo_file("b.jpg", "red")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(response.data["code"], "S087")
        self.assertEqual(data["features"], ["Borehole", "Gated"])
        self.assertEqual(data["photo_count"], 2)
        self.assertEqual([p["position"] for p in data["photos"]], [0, 1])
        self.assertIsNotNone(data["cover_photo"])
        self.assertTrue(data["is_published"])

        listing = PropertyListing.objects.get()
        self.assertEqual(listing.created_by, self.admin)

    def test_json_create_without_images(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.list_url,
            {
                "title": "Bedsitter",
                "listing_type": "RENT",
                "property_type": "APARTMENT",
                "price": "8000",
                "price_period": "MONTH",
                "location": "Juja",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["photos"], [])

    def test_rent_needs_price_period_and_sale_refuses_one(self):
        self.client.force_authenticate(self.admin)
        base = {
            "title": "Flat",
            "property_type": "APARTMENT",
            "price": "8000",
            "location": "Juja",
        }

        rent = self.client.post(self.list_url, {**base, "listing_type": "RENT"}, format="json")
        sale = self.client.post(
            self.list_url,
            {**base, "listing_type": "SALE", "price_period": "MONTH"},
            format="json",
        )

        self.assertEqual(rent.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("price_period", rent.data)
        self.assertEqual(sale.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("price_period", sale.data)

    def test_status_must_match_listing_type(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self.detail_url(self.listing()),
            {"status": "RENTED"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_plot_sets_estate_and_must_match_it(self):
        self.client.force_authenticate(self.admin)
        base = {
            "title": "Plot 14",
            "listing_type": "SALE",
            "property_type": "LAND",
            "price": "900000",
            "location": "Oak estate",
            "plot": self.plot.pk,
        }

        ok = self.client.post(self.list_url, base, format="json")
        clash = self.client.post(self.list_url, {**base, "estate": self.palm.pk}, format="json")

        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ok.data["data"]["estate"], self.oak.pk)
        self.assertEqual(ok.data["data"]["plot_number"], "14")
        self.assertEqual(clash.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("plot", clash.data)

    def test_invalid_image_is_rejected(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.list_url,
            {
                "title": "Bad",
                "listing_type": "SALE",
                "property_type": "HOUSE",
                "price": "100",
                "location": "X",
                "images": [SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PropertyListing.objects.exists())

    def test_only_admins_can_manage(self):
        listing = self.listing()

        for user in (None, self.member, self.manager):
            self.client.force_authenticate(user)

            create = self.client.post(self.list_url, {"title": "X"}, format="json")
            edit = self.client.patch(self.detail_url(listing), {"title": "Y"}, format="json")
            delete = self.client.delete(self.detail_url(listing))

            for response in (create, edit, delete):
                self.assertIn(
                    response.status_code,
                    {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
                )

        self.assertTrue(PropertyListing.objects.filter(pk=listing.pk, title="Oak villa").exists())


class BrowseListingTests(ListingTestCase):
    def test_public_sees_only_published(self):
        self.listing("Live")
        draft = self.listing("Draft", is_published=False)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "S085")
        self.assertEqual(self.results(response), ["Live"])
        self.assertEqual(
            self.client.get(self.detail_url(draft)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_admin_sees_drafts_and_can_filter_them(self):
        self.listing("Live")
        self.listing("Draft", is_published=False)
        self.client.force_authenticate(self.admin)

        everything = self.client.get(self.list_url)
        drafts = self.client.get(self.list_url, {"published": "false"})

        self.assertEqual(sorted(self.results(everything)), ["Draft", "Live"])
        self.assertEqual(self.results(drafts), ["Draft"])

    def test_filters(self):
        self.listing("Cheap house", price=2_000_000, bedrooms=2, county="Kiambu")
        self.listing("Big house", price=9_000_000, bedrooms=5, county="Kiambu", estate=self.oak)
        self.listing(
            "Flat",
            listing_type="RENT",
            property_type="APARTMENT",
            price=15_000,
            price_period="MONTH",
            bedrooms=1,
            county="Nairobi",
        )

        def titles(**params):
            return sorted(self.results(self.client.get(self.list_url, params)))

        self.assertEqual(titles(listing_type="RENT"), ["Flat"])
        self.assertEqual(titles(property_type="HOUSE"), ["Big house", "Cheap house"])
        self.assertEqual(titles(county="kiambu"), ["Big house", "Cheap house"])
        self.assertEqual(titles(min_price="1000000", max_price="5000000"), ["Cheap house"])
        self.assertEqual(titles(min_bedrooms="3"), ["Big house"])
        self.assertEqual(titles(estate=str(self.oak.pk)), ["Big house"])
        self.assertEqual(titles(search="oak"), ["Big house"])

    def test_ordering_and_featured_first(self):
        self.listing("A", price=300)
        self.listing("B", price=100)
        self.listing("C", price=200, is_featured=True)

        newest = self.results(self.client.get(self.list_url))
        cheapest = self.results(self.client.get(self.list_url, {"ordering": "price_asc"}))

        self.assertEqual(newest[0], "C")
        self.assertEqual(cheapest, ["B", "C", "A"])

    def test_bad_filters_are_rejected(self):
        for params in (
            {"listing_type": "SWAP"},
            {"min_price": "lots"},
            {"min_bedrooms": "-1"},
            {"ordering": "random"},
        ):
            response = self.client.get(self.list_url, params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, params)


class ListingPhotoTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        self.house = self.listing()

    def add(self, *files):
        return self.client.post(
            self.photos_url(self.house),
            {"images": list(files)},
            format="multipart",
        )

    def test_add_reorder_and_remove_photos(self):
        response = self.add(photo_file("a.jpg"), photo_file("b.jpg"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        first, second = self.house.photos.all()
        self.assertEqual((first.position, second.position), (0, 1))

        # Later uploads go after the existing ones.
        self.add(photo_file("c.jpg"))
        self.assertEqual(self.house.photos.last().position, 2)

        response = self.client.patch(
            self.photo_url(self.house, second),
            {"position": 0, "caption": "Front view"},
            format="json",
        )
        first.position = 5
        first.save()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.house.photos.first(), second)

        response = self.client.delete(self.photo_url(self.house, second))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["photo_count"], 2)

    def test_photo_limit(self):
        for position in range(20):
            ListingPhoto.objects.create(
                listing=self.house,
                image="x.jpg",
                width=1,
                height=1,
                position=position,
            )

        response = self.add(photo_file())

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E019")

    def test_photo_of_another_listing_is_not_found(self):
        other = self.listing("Other")
        self.client.post(self.photos_url(other), {"images": [photo_file()]}, format="multipart")
        photo = other.photos.get()

        response = self.client.delete(self.photo_url(self.house, photo))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_images_cannot_be_added_through_edit(self):
        response = self.client.patch(
            self.detail_url(self.house),
            {"images": [photo_file()]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("images", response.data)

    def test_delete_listing_removes_photos(self):
        self.add(photo_file())

        response = self.client.delete(self.detail_url(self.house))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ListingPhoto.objects.exists())
