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

from .models import GalleryImage


MEDIA_ROOT = tempfile.mkdtemp(prefix="gallery-tests-")

# EXIF tag ids.
ORIENTATION = 0x0112
GPS_INFO = 0x8825


def image_file(name="photo.jpg", format="JPEG", size=(800, 600), color="green",
               exif=None, mode="RGB"):
    buffer = io.BytesIO()
    picture = Image.new(mode, size, color)
    options = {"exif": exif} if exif is not None else {}
    picture.save(buffer, format=format, **options)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=f"image/{format.lower()}",
    )


def make_user(email, role):
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
class GalleryTestCase(APITestCase):
    list_url = reverse("gallery:image-list")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.member = make_user("member@example.com", User.Role.MEMBER)
        self.estate = Estate.objects.create(name="Oak", code="oak", location="Nairobi")

    def detail_url(self, image_id):
        return reverse("gallery:image-detail", args=[image_id])

    def upload(self, user=None, **fields):
        self.client.force_authenticate(user or self.admin)
        data = {"title": "Clubhouse", "image": image_file()}
        data.update(fields)
        return self.client.post(self.list_url, data, format="multipart")

    def uploaded(self, **fields):
        response = self.upload(**fields)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        self.client.force_authenticate(None)
        return GalleryImage.objects.get(pk=response.json()["data"]["id"])

    def stored(self, field):
        field.open("rb")
        try:
            return Image.open(io.BytesIO(field.read()))
        finally:
            field.close()


# ============================================================================
# UPLOADING
# ============================================================================

class UploadTests(GalleryTestCase):
    def test_admin_uploads_image(self):
        response = self.upload(caption="Opened in May", estate=self.estate.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["code"], "S042")

        data = body["data"]
        self.assertEqual(data["title"], "Clubhouse")
        self.assertEqual(data["estate_name"], "Oak")
        self.assertEqual((data["width"], data["height"]), (800, 600))
        self.assertTrue(data["image"].startswith("http://testserver/media/gallery/images/"))
        self.assertTrue(data["thumbnail"].endswith(".webp"))
        self.assertEqual(data["alt_text"], "Clubhouse")

        image = GalleryImage.objects.get()
        self.assertEqual(image.uploaded_by, self.admin)

    def test_form_uploads_are_published_unless_told_otherwise(self):
        # A missing checkbox in form data must not mean "unpublished".
        self.assertTrue(self.uploaded().is_published)
        self.assertFalse(self.uploaded(is_published="false").is_published)

    def test_only_admins_can_upload(self):
        for user in (self.manager, self.member):
            with self.subTest(role=user.role):
                self.assertEqual(
                    self.upload(user).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.client.force_authenticate(None)
        response = self.client.post(
            self.list_url,
            {"title": "x", "image": image_file()},
            format="multipart",
        )
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )
        self.assertFalse(GalleryImage.objects.exists())

    def test_original_file_name_is_not_published(self):
        image = self.uploaded(image=image_file(name="Jane Doe passport.jpg"))

        self.assertNotIn("Jane", image.image.name)
        self.assertNotIn("passport", image.image.name)

    def test_location_metadata_is_stripped(self):
        exif = Image.Exif()
        exif[GPS_INFO] = {1: "S", 2: (1.0, 17.0, 0.0)}
        exif[0x010F] = "PhoneMaker"  # camera make

        image = self.uploaded(image=image_file(exif=exif))

        stored = self.stored(image.image)
        self.assertEqual(dict(stored.getexif()), {})

    def test_camera_rotation_is_applied(self):
        exif = Image.Exif()
        exif[ORIENTATION] = 6  # rotate 90° clockwise to display

        image = self.uploaded(image=image_file(size=(800, 600), exif=exif))

        self.assertEqual((image.width, image.height), (600, 800))

    def test_large_images_are_resized(self):
        image = self.uploaded(image=image_file(size=(4000, 2000)))

        self.assertEqual((image.width, image.height), (2560, 1280))
        self.assertEqual(max(self.stored(image.thumbnail).size), 640)

    def test_png_and_webp_are_accepted(self):
        for name, image_format in (("a.png", "PNG"), ("b.webp", "WEBP")):
            with self.subTest(format=image_format):
                image = self.uploaded(
                    image=image_file(name=name, format=image_format, mode="RGBA", color=(0, 0, 0, 0)),
                )
                self.assertEqual(self.stored(image.image).format, image_format)

    def test_rejects_non_images_and_other_formats(self):
        fake = SimpleUploadedFile("photo.jpg", b"<?php echo 'hi'; ?>", content_type="image/jpeg")
        gif = image_file(name="a.gif", format="GIF", mode="P")

        for upload in (fake, gif):
            with self.subTest(file=upload.name):
                response = self.upload(image=upload)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("image", response.json())

        self.assertFalse(GalleryImage.objects.exists())

    def test_rejects_animated_images(self):
        buffer = io.BytesIO()
        frames = [Image.new("RGB", (50, 50), c) for c in ("red", "blue")]
        frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:])
        upload = SimpleUploadedFile("anim.webp", buffer.getvalue(), content_type="image/webp")

        response = self.upload(image=upload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(GALLERY_MAX_UPLOAD_MB=0)
    def test_rejects_oversized_files(self):
        response = self.upload()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("MB", response.json()["image"][0])

    def test_image_is_required(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.list_url, {"title": "No file"}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ============================================================================
# PUBLIC READING
# ============================================================================

class PublicReadTests(GalleryTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.uploaded(title="Gate", display_order=1)
        self.second = self.uploaded(title="Pool", display_order=0, estate=self.estate.pk)
        self.draft = self.uploaded(title="Draft", is_published=False)

    def titles(self, **params):
        response = self.client.get(self.list_url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [image["title"] for image in response.json()["data"]["results"]]

    def test_anyone_can_list_published_images_in_order(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.json()["code"], "S040")
        self.assertEqual(self.titles(), ["Pool", "Gate"])

    def test_signed_in_members_see_the_same(self):
        self.client.force_authenticate(self.member)

        self.assertEqual(self.titles(), ["Pool", "Gate"])

    def test_unpublished_images_are_hidden(self):
        response = self.client.get(self.detail_url(self.draft.pk))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.titles(published="false"), ["Pool", "Gate"])

    def test_admins_see_drafts_and_can_filter(self):
        self.client.force_authenticate(self.admin)

        self.assertEqual(set(self.titles()), {"Gate", "Pool", "Draft"})
        self.assertEqual(self.titles(published="false"), ["Draft"])

    def test_retrieve(self):
        response = self.client.get(self.detail_url(self.first.pk))

        self.assertEqual(response.json()["code"], "S041")
        self.assertEqual(response.json()["data"]["title"], "Gate")

    def test_filters(self):
        self.assertEqual(self.titles(estate=self.estate.pk), ["Pool"])
        self.assertEqual(self.titles(search="gat"), ["Gate"])

    def test_public_cannot_write(self):
        for method, url in (
            ("patch", self.detail_url(self.first.pk)),
            ("delete", self.detail_url(self.first.pk)),
            ("post", reverse("gallery:image-reorder")),
        ):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertIn(
                    response.status_code,
                    {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
                )

    def test_cors_allows_the_frontend(self):
        response = self.client.get(self.list_url, HTTP_ORIGIN="http://localhost:3000")
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://localhost:3000",
        )

        response = self.client.get(self.list_url, HTTP_ORIGIN="https://evil.example.com")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)


# ============================================================================
# MANAGING
# ============================================================================

class ManageTests(GalleryTestCase):
    def setUp(self):
        super().setUp()
        self.image = self.uploaded()
        self.client.force_authenticate(self.admin)

    def file_exists(self, field):
        return field.storage.exists(field.name)

    def test_edit_details(self):
        response = self.client.patch(
            self.detail_url(self.image.pk),
            {"caption": "New caption", "is_published": False},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S043")
        self.image.refresh_from_db()
        self.assertEqual(self.image.caption, "New caption")
        self.assertFalse(self.image.is_published)

    def test_replace_image_removes_old_files(self):
        old_image, old_thumb = self.image.image, self.image.thumbnail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                self.detail_url(self.image.pk),
                {"image": image_file(size=(300, 300))},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.image.refresh_from_db()
        self.assertEqual((self.image.width, self.image.height), (300, 300))
        self.assertFalse(self.file_exists(old_image))
        self.assertFalse(self.file_exists(old_thumb))
        self.assertTrue(self.file_exists(self.image.image))

    def test_delete_removes_row_and_files(self):
        image_file_field, thumb = self.image.image, self.image.thumbnail

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.detail_url(self.image.pk))

        self.assertEqual(response.json()["code"], "S044")
        self.assertFalse(GalleryImage.objects.exists())
        self.assertFalse(self.file_exists(image_file_field))
        self.assertFalse(self.file_exists(thumb))

    def test_reorder(self):
        second = self.uploaded(title="Second")
        third = self.uploaded(title="Third")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            reverse("gallery:image-reorder"),
            {"ids": [third.pk, self.image.pk, second.pk]},
            format="json",
        )

        self.assertEqual(response.json()["code"], "S045")
        self.assertEqual(
            list(GalleryImage.objects.values_list("pk", flat=True)),
            [third.pk, self.image.pk, second.pk],
        )

    def test_reorder_rejects_bad_ids(self):
        for ids in ([self.image.pk, self.image.pk], [self.image.pk, 99999], []):
            with self.subTest(ids=ids):
                response = self.client.post(
                    reverse("gallery:image-reorder"),
                    {"ids": ids},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_managers_cannot_edit(self):
        self.client.force_authenticate(self.manager)

        response = self.client.patch(
            self.detail_url(self.image.pk),
            {"title": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
