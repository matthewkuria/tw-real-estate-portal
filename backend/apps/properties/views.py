from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole
from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)
from apps.payments.models import Payment

from . import services
from .models import Property
from .serializers import (
    AllocateSerializer,
    PlotAllocationSerializer,
    PlotSerializer,
    ReleaseSerializer,
)


PLOT_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


class CanManagePlots(BasePermission):
    message = "Only Super Admins and Admins can manage and allocate plots."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in PLOT_ADMIN_ROLES
        )


def _int_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    if not value.isdigit():
        raise ValidationError({name: "Must be an id."})

    return int(value)


plot_response = envelope("PlotResponse", PlotSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List plots",
        description=(
            "Staff get every plot. Members get only the plots allocated to "
            "them, with what they've paid and the balance."
        ),
        parameters=[
            OpenApiParameter("estate", int),
            OpenApiParameter("status", str, enum=Property.Status.values),
            OpenApiParameter("member", int, description="Staff only: plots held by this member."),
            OpenApiParameter(
                "search",
                str,
                description="Staff only: plot number, or the holder's name or email.",
            ),
        ],
        responses={
            200: envelope("PlotListResponse", page_of("PlotPage", PlotSerializer)),
        },
    ),
    retrieve=extend_schema(summary="Get a plot", responses={200: plot_response}),
    create=extend_schema(summary="Add a plot", responses={201: plot_response}),
    partial_update=extend_schema(
        summary="Edit a plot",
        description="Allocation can't be changed here; use allocate and release.",
        responses={200: plot_response},
    ),
    destroy=extend_schema(
        summary="Delete a plot",
        description="Only for plots that were never allocated and have no payments or houses.",
        responses={200: envelope("EmptyResponse")},
    ),
)
class PlotViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Plots in the estates and who holds them. Super Admins and Admins add
    plots and allocate them; other staff read; members see their own.
    """

    serializer_class = PlotSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S093"
    list_description = "Plots retrieved"

    def get_permissions(self):
        if self.action == "allocations":
            return [IsStaffRole()]

        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]

        return [CanManagePlots()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Property.objects.none()

        user = self.request.user
        params = self.request.query_params

        queryset = Property.objects.select_related("estate", "allocated_to").annotate(
            amount_paid=Coalesce(
                Sum(
                    "payments__amount",
                    filter=Q(
                        payments__status=Payment.Status.CONFIRMED,
                        payments__member_id=F("allocated_to_id"),
                    ),
                ),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )

        staff = user.role in IsStaffRole.STAFF_ROLES

        if not staff:
            queryset = queryset.filter(allocated_to=user)

        estate = _int_param(params, "estate")
        if estate is not None:
            queryset = queryset.filter(estate_id=estate)

        plot_status = params.get("status")
        if plot_status is not None:
            if plot_status not in Property.Status.values:
                raise ValidationError({"status": "Unknown status."})

            queryset = queryset.filter(status=plot_status)

        if staff:
            member = _int_param(params, "member")
            if member is not None:
                queryset = queryset.filter(allocated_to_id=member)

            search = params.get("search")
            if search:
                queryset = queryset.filter(
                    Q(plot_number__iexact=search.strip())
                    | Q(allocated_to__first_name__icontains=search)
                    | Q(allocated_to__last_name__icontains=search)
                    | Q(allocated_to__email__icontains=search)
                )

        # Meta.ordering is dropped once Sum() is annotated; restate it.
        return queryset.order_by("estate__name", "plot_number", "id")

    def _data(self, plot):
        plot = self.get_queryset().get(pk=plot.pk)
        return PlotSerializer(plot, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success("S094", "Plot retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plot = serializer.save()

        return success("S095", "Plot added", self._data(plot), status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        plot = serializer.save()

        return success("S096", "Plot updated", self._data(plot))

    def destroy(self, request, *args, **kwargs):
        plot = self.get_object()

        if (
            plot.status == Property.Status.ALLOCATED
            or plot.allocations.exists()
            or plot.payments.exists()
            or plot.rental_houses.exists()
        ):
            return failure(
                "E024",
                "This plot has an allocation history, payments or houses, so it can't be deleted.",
                status.HTTP_409_CONFLICT,
            )

        plot.delete()

        return success("S097", "Plot deleted")

    # ------------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------------

    @extend_schema(
        summary="Allocate a plot to a member",
        description=(
            "The plot must be available and the member active. Their "
            "confirmed payments toward the estate must cover the required "
            "deposit, unless `waive_deposit` is set with `notes` explaining "
            "why. Payments they made toward the estate without a plot are "
            "linked to this one. The member is notified."
        ),
        request=AllocateSerializer,
        responses={200: plot_response},
    )
    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        plot = self.get_object()
        serializer = AllocateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            plot = services.allocate(
                plot,
                serializer.validated_data["member"],
                by=request.user,
                notes=serializer.validated_data["notes"],
                waive_deposit=serializer.validated_data["waive_deposit"],
            )
        except services.AllocationError as exc:
            return failure(exc.code, str(exc), status.HTTP_409_CONFLICT, exc.data)

        return success("S098", "Plot allocated", self._data(plot))

    @extend_schema(
        summary="Release a plot",
        description=(
            "Takes the plot back from its holder and makes it available. "
            "Their payments stay on record against the plot. The member is "
            "notified."
        ),
        request=ReleaseSerializer,
        responses={200: plot_response},
    )
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        plot = self.get_object()
        serializer = ReleaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            plot = services.release(plot, by=request.user, reason=serializer.validated_data["reason"])
        except services.AllocationError as exc:
            return failure(exc.code, str(exc), status.HTTP_409_CONFLICT, exc.data)

        return success("S099", "Plot released", self._data(plot))

    @extend_schema(
        summary="Allocation history",
        description="Staff only. Everyone who has held the plot, newest first.",
        responses={
            200: envelope("PlotAllocationListResponse", PlotAllocationSerializer(many=True)),
        },
    )
    @action(detail=True, methods=["get"])
    def allocations(self, request, pk=None):
        plot = self.get_object()
        history = plot.allocations.select_related("member", "allocated_by", "released_by")

        return success(
            "S100",
            "Allocation history retrieved",
            PlotAllocationSerializer(history, many=True).data,
        )
