from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)

from . import services
from .models import Payment
from .permissions import (
    CanApprovePayments,
    CanEditPayments,
    CanUsePayments,
    is_recorder,
)
from .serializers import PaymentSerializer, PaymentReasonSerializer


def _int_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    if not value.isdigit():
        raise ValidationError({name: "Must be an id."})

    return int(value)


def _date_param(params, name):
    value = params.get(name)

    if value is None:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({name: "Must be a date, YYYY-MM-DD."})


def _choice_param(params, name, choices):
    value = params.get(name)

    if value is not None and value not in choices.values:
        raise ValidationError({name: f"Unknown {name}."})

    return value


payment_response = envelope("PaymentResponse", PaymentSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List payments",
        description=(
            "Staff get every payment; members get their own. `total_confirmed` "
            "is the sum of confirmed payments matching the filters."
        ),
        parameters=[
            OpenApiParameter("member", int, description="Staff only."),
            OpenApiParameter("estate", int),
            OpenApiParameter("property", int, description="Plot id."),
            OpenApiParameter("status", str, enum=Payment.Status.values),
            OpenApiParameter("method", str, enum=Payment.Method.values),
            OpenApiParameter("received_from", str, description="YYYY-MM-DD, inclusive."),
            OpenApiParameter("received_to", str, description="YYYY-MM-DD, inclusive."),
            OpenApiParameter(
                "search",
                str,
                description="Staff only: reference, or the member's name or email.",
            ),
        ],
        responses={
            200: envelope(
                "PaymentListResponse",
                page_of(
                    "PaymentPage",
                    PaymentSerializer,
                    total_confirmed=serializers.DecimalField(max_digits=14, decimal_places=2),
                ),
            ),
        },
    ),
    retrieve=extend_schema(summary="Get a payment", responses={200: payment_response}),
    create=extend_schema(
        summary="Record a payment",
        description=(
            "Staff record a payment for a member; Super Admins, Admins and "
            "Finance Officers may set `confirm` to record it as confirmed. "
            "Members submit their own payments (leave out `member`) for "
            "staff to confirm. M-Pesa, bank and cheque payments need a "
            "reference not already used."
        ),
        responses={201: payment_response},
    ),
    partial_update=extend_schema(
        summary="Edit a pending payment",
        description="Staff only. Confirmed, rejected and reversed payments can't be edited.",
        responses={200: payment_response},
    ),
)
class PaymentViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Money members pay toward their estate and plot. Staff record and
    review payments; members see and submit their own.
    """

    serializer_class = PaymentSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    list_code = "S101"
    list_description = "Payments retrieved"

    def get_permissions(self):
        if self.action in ("confirm", "reject", "reverse"):
            return [CanApprovePayments()]

        if self.action == "partial_update":
            return [CanEditPayments()]

        return [CanUsePayments()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payment.objects.none()

        user = self.request.user
        params = self.request.query_params
        staff = is_recorder(user)

        queryset = Payment.objects.select_related(
            "member",
            "estate",
            "property",
            "recorded_by",
            "confirmed_by",
            "status_changed_by",
        )

        if not staff:
            queryset = queryset.filter(member=user)

        if self.action != "list":
            return queryset

        for name in ("estate", "property"):
            value = _int_param(params, name)
            if value is not None:
                queryset = queryset.filter(**{f"{name}_id": value})

        payment_status = _choice_param(params, "status", Payment.Status)
        if payment_status:
            queryset = queryset.filter(status=payment_status)

        method = _choice_param(params, "method", Payment.Method)
        if method:
            queryset = queryset.filter(method=method)

        received_from = _date_param(params, "received_from")
        if received_from:
            queryset = queryset.filter(received_at__date__gte=received_from)

        received_to = _date_param(params, "received_to")
        if received_to:
            queryset = queryset.filter(received_at__date__lte=received_to)

        if staff:
            member = _int_param(params, "member")
            if member is not None:
                queryset = queryset.filter(member_id=member)

            search = params.get("search", "").strip()
            if search:
                queryset = queryset.filter(
                    Q(reference__icontains=search)
                    | Q(member__first_name__icontains=search)
                    | Q(member__last_name__icontains=search)
                    | Q(member__email__icontains=search)
                )

        return queryset.order_by("-received_at", "-id")

    def get_list_extra(self, queryset):
        total = (
            queryset
            .filter(status=Payment.Status.CONFIRMED)
            .order_by()
            .aggregate(total=Sum("amount"))["total"]
        )

        return {"total_confirmed": f"{total or Decimal('0'):.2f}"}

    def _data(self, payment):
        payment = self.get_queryset().get(pk=payment.pk)
        return PaymentSerializer(payment, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success("S102", "Payment retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()

        return success("S103", "Payment recorded", self._data(payment), status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        payment = self.get_object()

        if payment.status != Payment.Status.PENDING:
            return failure(
                "E026",
                "Only pending payments can be edited.",
                status.HTTP_409_CONFLICT,
            )

        serializer = self.get_serializer(payment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()

        return success("S104", "Payment updated", self._data(payment))

    # ------------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------------

    def _transition(self, code, description, change, *args):
        try:
            payment = change(self.get_object(), self.request.user, *args)
        except services.PaymentError as exc:
            return failure(exc.code, str(exc), status.HTTP_409_CONFLICT)

        return success(code, description, self._data(payment))

    def _reason(self):
        serializer = PaymentReasonSerializer(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["reason"]

    @extend_schema(
        summary="Confirm a payment",
        description=(
            "Pending payments only. It then counts toward the member's "
            "deposit and plot balance. The member is notified."
        ),
        request=None,
        responses={200: payment_response},
    )
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return self._transition("S105", "Payment confirmed", services.confirm)

    @extend_schema(
        summary="Reject a payment",
        description="Pending payments only, e.g. the money never arrived. The member is notified.",
        request=PaymentReasonSerializer,
        responses={200: payment_response},
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        reason = self._reason()
        return self._transition("S106", "Payment rejected", services.reject, reason)

    @extend_schema(
        summary="Reverse a payment",
        description=(
            "Confirmed payments only, e.g. a bounced cheque. It stops "
            "counting toward the member's balance; an allocated plot stays "
            "allocated. The member is notified."
        ),
        request=PaymentReasonSerializer,
        responses={200: payment_response},
    )
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        reason = self._reason()
        return self._transition("S107", "Payment reversed", services.reverse, reason)
