from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
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
from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)

from .models import ListingPhoto, PropertyListing
from .serializers import (
    MAX_PHOTOS,
    ListingPhotoSerializer,
    PhotoUploadSerializer,
    PropertyListingDetailSerializer,
    PropertyListingSerializer,
    save_photos,
)


LISTING_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


def is_listing_admin(user):
    return user.is_authenticated and user.role in LISTING_ADMIN_ROLES


class CanManageListings(BasePermission):
    message = "Only Super Admins and Admins can manage property listings."

    def has_permission(self, request, view):
        return is_listing_admin(request.user)


def _int_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    if not value.isdigit():
        raise ValidationError({name: "Must be a whole number."})

    return int(value)


def _decimal_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValidationError({name: "Must be a number."})


def _choice_param(params, name, choices):
    value = params.get(name)

    if value is not None and value not in choices.values:
        raise ValidationError({name: f"Must be one of: {', '.join(choices.values)}."})

    return value


ORDERINGS = {
    # Featured first, then newest.
    "newest": ("-is_featured", "-created_at", "-id"),
    "price_asc": ("price", "-id"),
    "price_desc": ("-price", "-id"),
}


listing_response = envelope("PropertyListingResponse", PropertyListingDetailSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List properties for sale or rent",
        description=(
            "Public. Visitors get published listings; Super Admins and "
            "Admins also get unpublished ones and can filter by `published`."
        ),
        parameters=[
            OpenApiParameter("listing_type", str, enum=PropertyListing.ListingType.values),
            OpenApiParameter("property_type", str, enum=PropertyListing.PropertyType.values),
            OpenApiParameter("status", str, enum=PropertyListing.Status.values),
            OpenApiParameter("estate", int),
            OpenApiParameter("county", str),
            OpenApiParameter("min_price", float),
            OpenApiParameter("max_price", float),
            OpenApiParameter("min_bedrooms", int),
            OpenApiParameter("featured", bool, description="Only featured listings."),
            OpenApiParameter("published", bool, description="Admins only."),
            OpenApiParameter("search", str, description="Match in title, description or location."),
            OpenApiParameter("ordering", str, enum=list(ORDERINGS), description="Default: newest."),
        ],
        responses={
            200: envelope(
                "PropertyListingListResponse",
                page_of("PropertyListingPage", PropertyListingSerializer),
            ),
        },
    ),
    retrieve=extend_schema(
        summary="Get a listing with its photos",
        responses={200: listing_response},
    ),
    create=extend_schema(
        summary="List a property",
        description=(
            "JSON, or multipart/form-data to attach photos in the same "
            "request (repeat `images` once per file)."
        ),
        responses={201: listing_response},
    ),
    partial_update=extend_schema(summary="Edit a listing", responses={200: listing_response}),
    destroy=extend_schema(
        summary="Delete a listing and its photos",
        responses={200: envelope("EmptyResponse")},
    ),
)
class PropertyListingViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Properties advertised for sale or rent. Super Admins and Admins list
    and manage them; everyone else reads the published ones.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S085"
    list_description = "Listings retrieved"

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]

        return [CanManageListings()]

    def get_serializer_class(self):
        if self.action == "list":
            return PropertyListingSerializer

        return PropertyListingDetailSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PropertyListing.objects.none()

        params = self.request.query_params

        queryset = (
            PropertyListing.objects
            .select_related("estate", "plot")
            .prefetch_related("photos")
        )

        if is_listing_admin(self.request.user):
            published = params.get("published")

            if published is not None:
                if published not in {"true", "false"}:
                    raise ValidationError({"published": "Must be true or false."})

                queryset = queryset.filter(is_published=published == "true")
        else:
            queryset = queryset.filter(is_published=True)

        listing_type = _choice_param(params, "listing_type", PropertyListing.ListingType)
        if listing_type is not None:
            queryset = queryset.filter(listing_type=listing_type)

        property_type = _choice_param(params, "property_type", PropertyListing.PropertyType)
        if property_type is not None:
            queryset = queryset.filter(property_type=property_type)

        listing_status = _choice_param(params, "status", PropertyListing.Status)
        if listing_status is not None:
            queryset = queryset.filter(status=listing_status)

        estate = _int_param(params, "estate")
        if estate is not None:
            queryset = queryset.filter(estate_id=estate)

        county = params.get("county")
        if county:
            queryset = queryset.filter(county__iexact=county.strip())

        min_price = _decimal_param(params, "min_price")
        if min_price is not None:
            queryset = queryset.filter(price__gte=min_price)

        max_price = _decimal_param(params, "max_price")
        if max_price is not None:
            queryset = queryset.filter(price__lte=max_price)

        min_bedrooms = _int_param(params, "min_bedrooms")
        if min_bedrooms is not None:
            queryset = queryset.filter(bedrooms__gte=min_bedrooms)

        if params.get("featured") in {"true", "1"}:
            queryset = queryset.filter(is_featured=True)

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(location__icontains=search)
                | Q(county__icontains=search)
                | Q(estate__name__icontains=search)
            )

        ordering = params.get("ordering", "newest")

        if ordering not in ORDERINGS:
            raise ValidationError({"ordering": f"Must be one of: {', '.join(ORDERINGS)}."})

        return queryset.order_by(*ORDERINGS[ordering])

    def _data(self, listing):
        listing = (
            PropertyListing.objects
            .select_related("estate", "plot")
            .prefetch_related("photos")
            .get(pk=listing.pk)
        )
        return PropertyListingDetailSerializer(listing, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success("S086", "Listing retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            listing = serializer.save(created_by=request.user)

        return success("S087", "Property listed", self._data(listing), status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        listing = serializer.save()

        return success("S088", "Listing updated", self._data(listing))

    def destroy(self, request, *args, **kwargs):
        listing = self.get_object()

        with transaction.atomic():
            for photo in listing.photos.all():
                delete_files_after_commit(photo.image, photo.thumbnail)

            listing.delete()

        return success("S089", "Listing deleted")

    # ------------------------------------------------------------------------
    # Photos
    # ------------------------------------------------------------------------

    @extend_schema(
        summary="Add photos",
        description=(
            "multipart/form-data with one or more `images` (JPEG, PNG or "
            "WebP). They go after the existing photos. Metadata such as "
            f"location is removed. Up to {MAX_PHOTOS} photos per listing."
        ),
        request=PhotoUploadSerializer,
        responses={201: listing_response},
    )
    @action(detail=True, methods=["post"], url_path="photos")
    def add_photos(self, request, pk=None):
        listing = self.get_object()
        serializer = PhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        images = serializer.validated_data["images"]

        if listing.photos.count() + len(images) > MAX_PHOTOS:
            return failure(
                "E019",
                f"A listing can have at most {MAX_PHOTOS} photos.",
                status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            save_photos(listing, images)

        return success("S090", "Photos added", self._data(listing), status.HTTP_201_CREATED)

    @extend_schema(
        summary="Edit a photo's caption or position",
        description="Position 0 is the cover photo.",
        request=ListingPhotoSerializer,
        responses={200: listing_response},
    )
    @action(detail=True, methods=["patch"], url_path=r"photos/(?P<photo_id>\d+)")
    def update_photo(self, request, pk=None, photo_id=None):
        listing = self.get_object()
        photo = get_object_or_404(ListingPhoto, pk=photo_id, listing=listing)

        serializer = ListingPhotoSerializer(photo, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return success("S091", "Photo updated", self._data(listing))

    @extend_schema(
        summary="Remove a photo",
        request=None,
        responses={200: listing_response},
    )
    @update_photo.mapping.delete
    def remove_photo(self, request, pk=None, photo_id=None):
        listing = self.get_object()
        photo = get_object_or_404(ListingPhoto, pk=photo_id, listing=listing)

        with transaction.atomic():
            delete_files_after_commit(photo.image, photo.thumbnail)
            photo.delete()

        return success("S092", "Photo removed", self._data(listing))
