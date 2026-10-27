from django.db.models import Prefetch, Q
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole
from apps.community.access import member_estate_ids
from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)

from . import services
from .models import ServiceProvider, StatusChange
from .serializers import (
    BlacklistedMatch,
    ReasonSerializer,
    ResidentServiceProviderSerializer,
    ServiceProviderDetailSerializer,
    ServiceProviderSerializer,
)


PROVIDER_MANAGER_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
    User.Role.MANAGER,
}


def is_staff(user):
    return getattr(user, "role", None) in IsStaffRole.STAFF_ROLES


class CanManageProviders(BasePermission):
    message = "Only Super Admins, Admins and Managers can manage service providers."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in PROVIDER_MANAGER_ROLES
        )


detail_response = envelope("ServiceProviderResponse", ServiceProviderDetailSerializer())
blacklisted_match_response = envelope(
    "BlacklistedMatchResponse",
    inline_serializer(
        name="BlacklistedMatches",
        fields={
            "blacklisted_matches": inline_serializer(
                name="BlacklistedMatch",
                fields={
                    "id": serializers.IntegerField(),
                    "name": serializers.CharField(),
                    "matched_on": serializers.ListField(child=serializers.CharField()),
                },
                many=True,
            ),
        },
    ),
)


@extend_schema_view(
    list=extend_schema(
        summary="List service providers",
        description=(
            "Staff get every provider with full details. Residents get "
            "active providers that work anywhere or in an estate where they "
            "hold a plot, with contact details only (no notes, KRA PIN, "
            "registration number or blacklist details); `status` is ignored "
            "for them."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=ServiceProvider.Status.values),
            OpenApiParameter("category", str, enum=ServiceProvider.Category.values),
            OpenApiParameter(
                "estate",
                int,
                description="Providers working in this estate, including those that work anywhere.",
            ),
            OpenApiParameter(
                "search",
                str,
                description="Match in name, contact person, phone, email or KRA PIN.",
            ),
        ],
        responses={
            200: envelope(
                "ServiceProviderListResponse",
                page_of("ServiceProviderPage", ServiceProviderSerializer),
            ),
        },
    ),
    retrieve=extend_schema(
        summary="Get a service provider",
        description=(
            "Staff get full details and the blacklist history. Residents "
            "get the contact view, and only for active providers they can see."
        ),
        responses={200: detail_response},
    ),
    create=extend_schema(
        summary="Add a service provider",
        responses={201: detail_response, 409: blacklisted_match_response},
    ),
    partial_update=extend_schema(
        summary="Edit a service provider",
        responses={200: detail_response, 409: blacklisted_match_response},
    ),
)
class ServiceProviderViewSet(
    EnvelopeListMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Residents and staff can look providers up; Super Admins, Admins and
    Managers add, edit, blacklist and reinstate them. There's no delete:
    blacklisting keeps the record and the reason.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]

    list_code = "S067"
    list_description = "Service providers retrieved"

    def get_serializer_class(self):
        if not is_staff(self.request.user):
            return ResidentServiceProviderSerializer

        if self.action == "list":
            return ServiceProviderSerializer

        return ServiceProviderDetailSerializer

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]

        return [CanManageProviders()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ServiceProvider.objects.none()

        params = self.request.query_params

        queryset = (
            ServiceProvider.objects
            .select_related("created_by", "blacklisted_by")
            .prefetch_related(
                "estates",
                Prefetch(
                    "status_changes",
                    queryset=StatusChange.objects.select_related("changed_by"),
                ),
            )
        )

        staff = is_staff(self.request.user)

        if not staff:
            # Residents only see providers they could actually call on.
            queryset = queryset.filter(status=ServiceProvider.Status.ACTIVE).filter(
                Q(estates__isnull=True)
                | Q(estates__id__in=member_estate_ids(self.request.user))
            )

        provider_status = params.get("status")

        if provider_status is not None and staff:
            if provider_status not in ServiceProvider.Status.values:
                raise ValidationError({"status": "Unknown status."})

            queryset = queryset.filter(status=provider_status)

        category = params.get("category")

        if category is not None:
            if category not in ServiceProvider.Category.values:
                raise ValidationError({"category": "Unknown category."})

            queryset = queryset.filter(category=category)

        estate = params.get("estate")

        if estate is not None:
            if not estate.isdigit():
                raise ValidationError({"estate": "Must be an estate id."})

            queryset = queryset.filter(
                Q(estates__id=int(estate)) | Q(estates__isnull=True)
            )

        search = params.get("search")

        if search:
            phone = services.phone_key(search)
            match = (
                Q(name__icontains=search)
                | Q(contact_person__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )

            if staff:
                match |= Q(kra_pin__iexact=search.strip())

            if len(phone) >= 4:
                match |= Q(phone__icontains=phone)

            queryset = queryset.filter(match)

        return queryset.distinct()

    def _data(self, provider):
        provider = self.get_queryset().get(pk=provider.pk)
        return ServiceProviderDetailSerializer(provider, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success(
            "S068",
            "Service provider retrieved",
            self.get_serializer(self.get_object()).data,
        )

    def _validate(self, serializer):
        """
        Validate, or return the E017 response when the details match a
        blacklisted provider.
        """
        try:
            serializer.is_valid(raise_exception=True)
        except BlacklistedMatch as exc:
            return failure(
                "E017",
                (
                    "These details match a blacklisted provider. Resend "
                    "with allow_blacklisted_match=true to save anyway."
                ),
                status.HTTP_409_CONFLICT,
                {"blacklisted_matches": exc.matches},
            )

        return None

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if (blocked := self._validate(serializer)) is not None:
            return blocked

        provider = serializer.save(created_by=request.user)

        return success(
            "S069",
            "Service provider added",
            self._data(provider),
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        provider = self.get_object()
        serializer = self.get_serializer(provider, data=request.data, partial=True)

        if (blocked := self._validate(serializer)) is not None:
            return blocked

        provider = serializer.save()

        return success("S070", "Service provider updated", self._data(provider))

    @extend_schema(
        summary="Blacklist a service provider",
        request=ReasonSerializer,
        responses={200: detail_response},
    )
    @action(detail=True, methods=["post"])
    def blacklist(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            provider = services.blacklist(
                self.get_object(),
                serializer.validated_data["reason"],
                request.user,
            )
        except services.InvalidTransition as exc:
            return failure("E015", str(exc), status.HTTP_409_CONFLICT)

        return success("S071", "Service provider blacklisted", self._data(provider))

    @extend_schema(
        summary="Take a provider off the blacklist",
        request=ReasonSerializer,
        responses={200: detail_response},
    )
    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            provider = services.reinstate(
                self.get_object(),
                serializer.validated_data["reason"],
                request.user,
            )
        except services.InvalidTransition as exc:
            return failure("E016", str(exc), status.HTTP_409_CONFLICT)

        return success("S072", "Service provider reinstated", self._data(provider))
