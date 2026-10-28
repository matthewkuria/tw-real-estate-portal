from django.db import transaction
from django.db.models import Count, Q
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
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole
from apps.common.files import delete_files_after_commit
from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)

from .models import HousePhoto, RentalHouse, Tenant
from .serializers import (
    HousePhotoSerializer,
    RentalHouseDetailSerializer,
    RentalHouseSerializer,
    TenantSerializer,
)


RENTAL_MANAGER_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.MANAGER,
}


class CanManageRentals(BasePermission):
    message = "Only Super Admins, Admins and Managers can change the rental register."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in RENTAL_MANAGER_ROLES
        )


class RentalPermissionsMixin:
    """
    The register is staff-only: any staff member can read it; Super
    Admins, Admins and Managers can change it.
    """

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsStaffRole()]

        return [CanManageRentals()]


def _int_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    if not value.isdigit():
        raise ValidationError({name: "Must be an id."})

    return int(value)


# ============================================================================
# HOUSES
# ============================================================================

house_response = envelope("RentalHouseResponse", RentalHouseDetailSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List rented houses",
        parameters=[
            OpenApiParameter("estate", int),
            OpenApiParameter("property", int, description="Only houses on this plot."),
            OpenApiParameter("landlord", int, description="Only houses on plots allocated to this user."),
            OpenApiParameter("house_type", str, enum=RentalHouse.HouseType.values),
            OpenApiParameter("bedrooms", int),
            OpenApiParameter("occupied", bool, description="true: has tenants; false: empty."),
            OpenApiParameter(
                "search",
                str,
                description="Match in house number, plot number, estate, landlord or tenant name/phone.",
            ),
        ],
        responses={
            200: envelope(
                "RentalHouseListResponse",
                page_of("RentalHousePage", RentalHouseSerializer),
            ),
        },
    ),
    retrieve=extend_schema(
        summary="Get a house with its tenants and photos",
        responses={200: house_response},
    ),
    create=extend_schema(summary="Add a rented house", responses={201: house_response}),
    partial_update=extend_schema(summary="Edit a rented house", responses={200: house_response}),
    destroy=extend_schema(
        summary="Remove a rented house",
        description="Only possible once it has no tenants.",
        responses={200: envelope("EmptyResponse")},
    ),
)
class RentalHouseViewSet(RentalPermissionsMixin, EnvelopeListMixin, viewsets.ModelViewSet):
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    list_code = "S073"
    list_description = "Rented houses retrieved"

    def get_serializer_class(self):
        if self.action == "list":
            return RentalHouseSerializer

        return RentalHouseDetailSerializer

    def get_queryset(self):
        params = self.request.query_params

        queryset = (
            RentalHouse.objects
            .select_related("property__estate", "property__allocated_to")
            .prefetch_related("tenants", "photos")
        )

        estate = _int_param(params, "estate")
        if estate is not None:
            queryset = queryset.filter(property__estate_id=estate)

        prop = _int_param(params, "property")
        if prop is not None:
            queryset = queryset.filter(property_id=prop)

        landlord = _int_param(params, "landlord")
        if landlord is not None:
            queryset = queryset.filter(property__allocated_to_id=landlord)

        house_type = params.get("house_type")
        if house_type is not None:
            if house_type not in RentalHouse.HouseType.values:
                raise ValidationError({"house_type": "Unknown house type."})

            queryset = queryset.filter(house_type=house_type)

        bedrooms = _int_param(params, "bedrooms")
        if bedrooms is not None:
            queryset = queryset.filter(bedrooms=bedrooms)

        occupied = params.get("occupied")
        if occupied is not None:
            if occupied not in {"true", "false"}:
                raise ValidationError({"occupied": "Must be true or false."})

            queryset = queryset.annotate(tenant_total=Count("tenants", distinct=True))

            if occupied == "true":
                queryset = queryset.filter(tenant_total__gt=0)
            else:
                queryset = queryset.filter(tenant_total=0)

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(house_number__icontains=search)
                | Q(property__plot_number__iexact=search)
                | Q(property__estate__name__icontains=search)
                | Q(property__allocated_to__first_name__icontains=search)
                | Q(property__allocated_to__last_name__icontains=search)
                | Q(tenants__full_name__icontains=search)
                | Q(tenants__phone__icontains=search)
            ).distinct()

        # Meta.ordering is dropped once Count() is annotated; restate it.
        return queryset.order_by(*RentalHouse._meta.ordering)

    def _data(self, house):
        house = self.get_queryset().get(pk=house.pk)
        return RentalHouseDetailSerializer(house, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success("S074", "House retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        house = serializer.save(created_by=request.user)

        return success("S075", "House added", self._data(house), status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        house = serializer.save()

        return success("S076", "House updated", self._data(house))

    def destroy(self, request, *args, **kwargs):
        house = self.get_object()

        if house.tenants.exists():
            return failure(
                "E018",
                "Move or remove this house's tenants before removing it.",
                status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            for photo in house.photos.all():
                delete_files_after_commit(photo.image, photo.thumbnail)

            house.delete()

        return success("S077", "House removed")

    # ------------------------------------------------------------------------
    # Photos
    # ------------------------------------------------------------------------

    @extend_schema(
        summary="Add a photo",
        description=(
            "multipart/form-data with `image` (JPEG, PNG or WebP) and an "
            "optional `caption`. Metadata such as location is removed."
        ),
        request=HousePhotoSerializer,
        responses={201: house_response},
    )
    @action(detail=True, methods=["post"], url_path="photos")
    def add_photo(self, request, pk=None):
        house = self.get_object()
        serializer = HousePhotoSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save(house=house)

        return success("S078", "Photo added", self._data(house), status.HTTP_201_CREATED)

    @extend_schema(
        summary="Remove a photo",
        request=None,
        responses={200: house_response},
    )
    @action(detail=True, methods=["delete"], url_path=r"photos/(?P<photo_id>\d+)")
    def remove_photo(self, request, pk=None, photo_id=None):
        house = self.get_object()
        photo = get_object_or_404(HousePhoto, pk=photo_id, house=house)

        with transaction.atomic():
            delete_files_after_commit(photo.image, photo.thumbnail)
            photo.delete()

        return success("S079", "Photo removed", self._data(house))


# ============================================================================
# TENANTS
# ============================================================================

tenant_response = envelope("TenantResponse", TenantSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List tenants",
        parameters=[
            OpenApiParameter("estate", int),
            OpenApiParameter("house", int),
            OpenApiParameter("has_account", bool, description="Linked to a portal account or not."),
            OpenApiParameter(
                "search",
                str,
                description="Match in name, phone, email or ID number.",
            ),
        ],
        responses={
            200: envelope("TenantListResponse", page_of("TenantPage", TenantSerializer)),
        },
    ),
    retrieve=extend_schema(summary="Get a tenant", responses={200: tenant_response}),
    create=extend_schema(summary="Add a tenant", responses={201: tenant_response}),
    partial_update=extend_schema(
        summary="Edit a tenant",
        description="Change `house` to move the tenant to another house.",
        responses={200: tenant_response},
    ),
    destroy=extend_schema(
        summary="Remove a tenant (moved out)",
        responses={200: envelope("EmptyResponse")},
    ),
)
class TenantViewSet(RentalPermissionsMixin, EnvelopeListMixin, viewsets.ModelViewSet):
    serializer_class = TenantSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S080"
    list_description = "Tenants retrieved"

    def get_queryset(self):
        params = self.request.query_params

        queryset = Tenant.objects.select_related(
            "house__property__estate",
            "user",
        )

        estate = _int_param(params, "estate")
        if estate is not None:
            queryset = queryset.filter(house__property__estate_id=estate)

        house = _int_param(params, "house")
        if house is not None:
            queryset = queryset.filter(house_id=house)

        has_account = params.get("has_account")
        if has_account is not None:
            if has_account not in {"true", "false"}:
                raise ValidationError({"has_account": "Must be true or false."})

            queryset = queryset.filter(user__isnull=has_account == "false")

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
                | Q(id_number__iexact=search.strip())
            )

        return queryset

    def retrieve(self, request, *args, **kwargs):
        return success(
            "S081",
            "Tenant retrieved",
            self.get_serializer(self.get_object()).data,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = serializer.save(created_by=request.user)

        return success(
            "S082",
            "Tenant added",
            self.get_serializer(tenant).data,
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        tenant = serializer.save()

        return success("S083", "Tenant updated", self.get_serializer(tenant).data)

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()

        return success("S084", "Tenant removed")
