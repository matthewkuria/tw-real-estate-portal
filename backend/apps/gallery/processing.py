"""
Cleans uploaded images before they're published.

Every upload is re-encoded, which:
- proves the file really is an image of an allowed type,
- drops EXIF metadata (phone photos often carry GPS coordinates),
- applies the camera's rotation so the picture displays upright,
- caps the size, and produces a small WebP thumbnail for grids.
"""

import io
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

# Refuse absurd dimensions before decoding (guards against "decompression
# bomb" files that are tiny on disk but huge in memory).
MAX_PIXELS = 50_000_000

MAX_DIMENSION = 2560
THUMBNAIL_DIMENSION = 640


class InvalidImage(ValueError):
    pass


@dataclass
class ProcessedImage:
    image: ContentFile
    thumbnail: ContentFile


def process_upload(upload):
    max_bytes = settings.GALLERY_MAX_UPLOAD_MB * 1024 * 1024

    if upload.size > max_bytes:
        raise InvalidImage(
            f"Images must be {settings.GALLERY_MAX_UPLOAD_MB} MB or smaller."
        )

    try:
        upload.seek(0)
        with Image.open(upload) as probe:
            image_format = probe.format
            width, height = probe.size
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise InvalidImage("Upload a valid JPEG, PNG or WebP image.")

    if image_format not in ALLOWED_FORMATS:
        raise InvalidImage("Only JPEG, PNG and WebP images are allowed.")

    if width * height > MAX_PIXELS:
        raise InvalidImage("This image's dimensions are too large.")

    # verify() leaves the file unusable, so open it again to decode.
    upload.seek(0)

    with Image.open(upload) as source:
        if getattr(source, "n_frames", 1) > 1:
            raise InvalidImage("Animated images aren't supported.")

        icc_profile = source.info.get("icc_profile")
        picture = ImageOps.exif_transpose(source)
        picture.load()

    main = picture.copy()
    main.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    thumb = picture.copy()
    thumb.thumbnail((THUMBNAIL_DIMENSION, THUMBNAIL_DIMENSION), Image.Resampling.LANCZOS)

    extension = ALLOWED_FORMATS[image_format]

    return ProcessedImage(
        image=ContentFile(
            _encode(main, image_format, icc_profile),
            name=f"image{extension}",
        ),
        thumbnail=ContentFile(
            _encode(thumb, "WEBP", icc_profile),
            name="thumbnail.webp",
        ),
    )


def _encode(picture, image_format, icc_profile):
    """
    Save without the original metadata. Only the colour profile is kept,
    so colours don't shift.
    """
    options = {}

    if icc_profile:
        options["icc_profile"] = icc_profile

    if image_format == "JPEG":
        if picture.mode != "RGB":
            picture = picture.convert("RGB")
        options.update(quality=85, optimize=True, progressive=True)
    elif image_format == "WEBP":
        if picture.mode not in {"RGB", "RGBA"}:
            picture = picture.convert("RGBA" if "A" in picture.getbands() else "RGB")
        options.update(quality=82, method=4)
    else:
        options.update(optimize=True)

    buffer = io.BytesIO()
    picture.save(buffer, format=image_format, **options)
    return buffer.getvalue()
