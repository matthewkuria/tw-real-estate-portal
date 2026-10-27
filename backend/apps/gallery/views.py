from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission

from apps.accounts.models import User
from apps.common.files import delete_files_after_commit
from apps.common.responses import EnvelopeListMixin, envelope, page_of, success

from .models import GalleryImage
from .serializers import GalleryImageSerializer, ReorderSerializer


GALLERY_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


def is_gallery_admin(user):
    return user.is_authenticated and user.role in GALLERY_ADMIN_ROLES


class CanManageGallery(BasePermission):
    message = "Only Super Admins and Admins can manage the gallery."

    def has_permission(self, request, view):
        return is_gallery_admin(request.user)


image_response = envelope("GalleryImageResponse", GalleryImageSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List gallery images",
        description=(
            "Public. Returns published images in display order. Admins also "
            "see unpublished images and can filter with `published`."
        ),
        parameters=[
            OpenApiParameter("estate", int, description="Only this estate's images."),
            OpenApiParameter("search", str, description="Match in title or caption."),
            OpenApiParameter(
                "published",
                bool,
                description="Admins only: filter by published state.",
            ),
        ],
        responses={
            200: envelope(
                "GalleryImageListResponse",
                page_of("GalleryImagePage", GalleryImageSerializer),
            ),
        },
    ),
    retrieve=extend_schema(summary="Get a gallery image", responses={200: image_response}),
    create=extend_schema(
        summary="Upload a gallery image",
        description=(
            "Admins only. Send `multipart/form-data` with an `image` file "
            "(JPEG, PNG or WebP). The image is re-encoded without its "
            "metadata, resized to at most 2560px, and given a 640px "
            "thumbnail."
        ),
        responses={201: image_response},
    ),
    partial_update=extend_schema(
        summary="Edit a gallery image",
        description=(
            "Admins only. JSON for details, or multipart to replace the "
            "image file."
        ),
        responses={200: image_response},
    ),
    destroy=extend_schema(
        summary="Delete a gallery image",
        responses={200: envelope("EmptyResponse")},
    ),
)
class GalleryImageViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    The public gallery shown on the website and in the portal. Anyone can
    read published images; only Super Admins and Admins can change it.
    """

    serializer_class = GalleryImageSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S040"
    list_description = "Gallery images retrieved"

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]

        return [CanManageGallery()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return GalleryImage.objects.none()

        params = self.request.query_params
        queryset = GalleryImage.objects.select_related("estate")

        if is_gallery_admin(self.request.user):
            published = params.get("published")

            if published in {"true", "1"}:
                queryset = queryset.filter(is_published=True)
            elif published in {"false", "0"}:
                queryset = queryset.filter(is_published=False)
        else:
            queryset = queryset.filter(is_published=True)

        estate = params.get("estate")

        if estate is not None:
            if not estate.isdigit():
                raise ValidationError({"estate": "Must be an estate id."})

            queryset = queryset.filter(estate_id=int(estate))

        search = params.get("search")

        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(caption__icontains=search)
            )

        return queryset

    def retrieve(self, request, *args, **kwargs):
        return success(
            "S041",
            "Gallery image retrieved",
            self.get_serializer(self.get_object()).data,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = serializer.save(uploaded_by=request.user)

        return success(
            "S042",
            "Gallery image uploaded",
            self.get_serializer(image).data,
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        image = self.get_object()
        old_files = (image.image, image.thumbnail)

        serializer = self.get_serializer(image, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            image = serializer.save()

            if "image" in serializer.validated_data:
                delete_files_after_commit(*old_files)

        return success(
            "S043",
            "Gallery image updated",
            self.get_serializer(image).data,
        )

    def destroy(self, request, *args, **kwargs):
        image = self.get_object()

        with transaction.atomic():
            delete_files_after_commit(image.image, image.thumbnail)
            image.delete()

        return success("S044", "Gallery image deleted")

    @extend_schema(
        summary="Reorder gallery images",
        description=(
            "Admins only. Send image ids in the order they should be shown; "
            "they get display positions 0, 1, 2... Images not listed keep "
            "their current position."
        ),
        request=ReorderSerializer,
        responses={200: envelope("EmptyResponse")},
    )
    @action(detail=False, methods=["post"], parser_classes=[JSONParser])
    def reorder(self, request):
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            for position, image_id in enumerate(serializer.validated_data["ids"]):
                GalleryImage.objects.filter(pk=image_id).update(display_order=position)

        return success("S045", "Gallery reordered")
