from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.common.responses import EnvelopeListMixin, envelope, page_of, success

from . import services
from .models import Notification
from .serializers import (
    MarkedSerializer,
    NotificationSerializer,
    PreferenceSerializer,
    PreferenceUpdateSerializer,
    UnreadCountSerializer,
)


marked_response = envelope("MarkedResponse", MarkedSerializer())


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("unread", bool, description="Only unread notifications."),
        ],
        responses={
            200: envelope(
                "NotificationListResponse",
                page_of(
                    "NotificationPage",
                    NotificationSerializer,
                    unread_count=serializers.IntegerField(),
                ),
            ),
        },
    ),
)
class NotificationViewSet(EnvelopeListMixin, viewsets.GenericViewSet):
    """
    The signed-in user's notifications, newest first.
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    list_code = "S033"
    list_description = "Notifications retrieved"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()

        queryset = (
            Notification.objects
            .filter(recipient=self.request.user)
            .select_related("actor")
        )

        if self.request.query_params.get("unread") in {"1", "true", "True"}:
            queryset = queryset.filter(read_at__isnull=True)

        return queryset

    def get_list_extra(self, queryset):
        return {"unread_count": services.unread_count(self.request.user)}

    @extend_schema(
        summary="Unread notification count",
        responses={200: envelope("UnreadCountResponse", UnreadCountSerializer())},
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return success(
            "S034",
            "Unread count retrieved",
            {"unread_count": services.unread_count(request.user)},
        )

    @extend_schema(
        summary="Mark one notification as read",
        request=None,
        responses={200: marked_response},
    )
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        marked = services.mark_read(request.user, [notification.pk])

        return success(
            "S035",
            "Notification marked as read",
            {"marked": marked, "unread_count": services.unread_count(request.user)},
        )

    @extend_schema(
        summary="Mark all notifications as read",
        request=None,
        responses={200: marked_response},
    )
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        marked = services.mark_read(request.user)

        return success(
            "S036",
            "All notifications marked as read",
            {"marked": marked, "unread_count": 0},
        )


preferences_response = envelope(
    "NotificationPreferencesResponse",
    PreferenceSerializer(many=True),
)


class NotificationPreferencesView(APIView):
    """
    Which notification emails the signed-in user receives. Types that are
    in-app only, or whose emails can't be turned off, are listed with
    `sends_email` / `can_opt_out` so the UI can show them disabled.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get email preferences",
        responses={200: preferences_response},
    )
    def get(self, request):
        return success(
            "S037",
            "Notification preferences retrieved",
            services.preferences_for(request.user),
        )

    @extend_schema(
        summary="Update email preferences",
        request=PreferenceUpdateSerializer,
        responses={200: preferences_response},
    )
    def patch(self, request):
        serializer = PreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.set_preferences(request.user, serializer.validated_data["preferences"])

        return success(
            "S038",
            "Notification preferences updated",
            services.preferences_for(request.user),
        )
