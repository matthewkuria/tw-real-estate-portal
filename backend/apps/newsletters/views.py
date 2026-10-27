from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)

from . import services
from .models import Newsletter, Subscriber
from .serializers import (
    ArchiveSerializer,
    NewsletterDetailSerializer,
    NewsletterSerializer,
    PreviewSerializer,
    ScheduleSerializer,
    SubscriptionSerializer,
    TestSendSerializer,
    TokenStatusSerializer,
)


NEWSLETTER_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


class CanManageNewsletters(BasePermission):
    message = "Only Super Admins and Admins can manage newsletters."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in NEWSLETTER_ADMIN_ROLES
        )


def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}"


detail_response = envelope("NewsletterResponse", NewsletterDetailSerializer())
empty_response = envelope("EmptyResponse")


# ============================================================================
# ADMIN: WRITING AND SENDING
# ============================================================================

@extend_schema_view(
    list=extend_schema(
        summary="List newsletters",
        parameters=[
            OpenApiParameter(
                "status",
                str,
                enum=Newsletter.Status.values,
                description="Only newsletters in this state.",
            ),
        ],
        responses={
            200: envelope(
                "NewsletterListResponse",
                page_of("NewsletterPage", NewsletterSerializer),
            ),
        },
    ),
    retrieve=extend_schema(summary="Get a newsletter", responses={200: detail_response}),
    create=extend_schema(summary="Create a draft", responses={201: detail_response}),
    partial_update=extend_schema(
        summary="Edit a draft or scheduled newsletter",
        responses={200: detail_response},
    ),
    destroy=extend_schema(
        summary="Delete a draft or scheduled newsletter",
        responses={200: empty_response},
    ),
)
class NewsletterViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Super Admins and Admins write newsletters as drafts, preview and
    test-send them, then send now or schedule for later. Sent newsletters
    can't be edited or deleted.
    """

    permission_classes = [CanManageNewsletters]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S046"
    list_description = "Newsletters retrieved"

    def get_serializer_class(self):
        if self.action == "list":
            return NewsletterSerializer

        return NewsletterDetailSerializer

    def get_queryset(self):
        queryset = Newsletter.objects.select_related("estate", "created_by")
        newsletter_status = self.request.query_params.get("status")

        if newsletter_status is not None:
            if newsletter_status not in Newsletter.Status.values:
                raise ValidationError({"status": "Unknown status."})

            queryset = queryset.filter(status=newsletter_status)

        return queryset

    def _data(self, newsletter):
        return NewsletterDetailSerializer(newsletter, context=self.get_serializer_context()).data

    def retrieve(self, request, *args, **kwargs):
        return success("S047", "Newsletter retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        newsletter = serializer.save(created_by=request.user)

        return success(
            "S048",
            "Draft created",
            self._data(newsletter),
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        newsletter = self.get_object()

        try:
            services.ensure_editable(newsletter)
        except services.NotEditable as exc:
            return failure("E012", str(exc), status.HTTP_409_CONFLICT)

        serializer = self.get_serializer(newsletter, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        newsletter = serializer.save()

        return success("S049", "Newsletter updated", self._data(newsletter))

    def destroy(self, request, *args, **kwargs):
        newsletter = self.get_object()

        try:
            services.ensure_editable(newsletter)
        except services.NotEditable as exc:
            return failure("E012", str(exc), status.HTTP_409_CONFLICT)

        newsletter.delete()

        return success("S050", "Newsletter deleted")

    @extend_schema(
        summary="Preview the email",
        description="The rendered HTML and text, and how many people a send would reach now.",
        responses={200: envelope("NewsletterPreviewResponse", PreviewSerializer())},
    )
    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        newsletter = self.get_object()
        html_body, text_body = services.render(newsletter)

        return success(
            "S051",
            "Preview rendered",
            {
                "html": html_body,
                "text": text_body,
                "recipient_count": services.recipient_count(newsletter),
            },
        )

    @extend_schema(
        summary="Send a test copy",
        request=TestSendSerializer,
        responses={200: empty_response},
    )
    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        newsletter = self.get_object()
        serializer = TestSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        emails = serializer.validated_data.get("emails") or [request.user.email]
        services.send_test(newsletter, emails)

        return success("S052", f"Test sent to {', '.join(emails)}")

    @extend_schema(
        summary="Send now or schedule",
        description=(
            "Without `send_at`, emails are queued immediately and the worker "
            "delivers them. With `send_at`, the newsletter is scheduled and "
            "goes out at that time."
        ),
        request=ScheduleSerializer,
        responses={200: detail_response},
    )
    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        newsletter = self.get_object()
        serializer = ScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        send_at = serializer.validated_data.get("send_at")

        try:
            if send_at is None:
                newsletter = services.send_now(newsletter, request.user)
                code, description = "S053", "Newsletter queued for sending"
            else:
                newsletter = services.schedule(newsletter, send_at, request.user)
                code, description = "S054", "Newsletter scheduled"
        except services.NotEditable as exc:
            return failure("E012", str(exc), status.HTTP_409_CONFLICT)
        except services.CannotSend as exc:
            return failure("E013", str(exc), status.HTTP_400_BAD_REQUEST)

        return success(code, description, self._data(newsletter))

    @extend_schema(
        summary="Cancel a scheduled send",
        description="Returns the newsletter to draft.",
        request=None,
        responses={200: detail_response},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            newsletter = services.cancel_schedule(self.get_object())
        except services.NotEditable as exc:
            return failure("E012", str(exc), status.HTTP_409_CONFLICT)

        return success("S055", "Scheduled send cancelled", self._data(newsletter))


# ============================================================================
# MEMBERS: READING PAST ISSUES
# ============================================================================

@extend_schema_view(
    list=extend_schema(
        summary="Past newsletters",
        responses={
            200: envelope(
                "NewsletterArchiveListResponse",
                page_of("NewsletterArchivePage", ArchiveSerializer),
            ),
        },
    ),
    retrieve=extend_schema(
        summary="Read a past newsletter",
        responses={200: envelope("NewsletterArchiveResponse", ArchiveSerializer())},
    ),
)
class ArchiveViewSet(
    EnvelopeListMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Sent newsletters the signed-in user was in the audience for, newest
    first. Staff see every sent newsletter.
    """

    serializer_class = ArchiveSerializer
    permission_classes = [IsAuthenticated]

    list_code = "S056"
    list_description = "Newsletters retrieved"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Newsletter.objects.none()

        user = self.request.user
        queryset = Newsletter.objects.filter(status=Newsletter.Status.SENT).order_by("-sent_at")
        Audience = Newsletter.Audience

        if user.role != User.Role.MEMBER:
            return queryset

        return queryset.filter(
            Q(audience__in=[Audience.EVERYONE, Audience.ALL_MEMBERS])
            | Q(
                audience=Audience.ESTATE_MEMBERS,
                estate__properties__allocated_to=user,
            )
        ).distinct()

    def retrieve(self, request, *args, **kwargs):
        return success(
            "S057",
            "Newsletter retrieved",
            self.get_serializer(self.get_object()).data,
        )


# ============================================================================
# SUBSCRIPTIONS
# ============================================================================

subscription_response = envelope("NewsletterSubscriptionResponse", SubscriptionSerializer())
token_status_response = envelope("UnsubscribeStatusResponse", TokenStatusSerializer())


class SubscriptionView(APIView):
    """
    The signed-in user's own newsletter setting.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Am I subscribed?", responses={200: subscription_response})
    def get(self, request):
        subscriber = services.subscription_for(request.user)

        return success(
            "S058",
            "Subscription retrieved",
            {"subscribed": subscriber.status == Subscriber.Status.SUBSCRIBED},
        )

    @extend_schema(
        summary="Subscribe or unsubscribe",
        request=SubscriptionSerializer,
        responses={200: subscription_response},
    )
    def patch(self, request):
        serializer = SubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        subscriber = services.set_subscribed(
            services.subscription_for(request.user),
            serializer.validated_data["subscribed"],
        )

        return success(
            "S059",
            "Subscription updated",
            {"subscribed": subscriber.status == Subscriber.Status.SUBSCRIBED},
        )


class UnsubscribeView(APIView):
    """
    The link in every newsletter. Works without signing in: the token in
    the URL identifies the subscriber.

    Mail apps' one-click unsubscribe (RFC 8058) POSTs here directly with
    the form body `List-Unsubscribe=One-Click`. The Next.js page at
    /newsletter/unsubscribe?token=... can GET the status and POST
    `{"subscribed": false}` (or `true` to undo).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "newsletter_unsubscribe"
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _subscriber(self, token):
        return get_object_or_404(Subscriber, token=token)

    def _data(self, subscriber):
        return {
            "email": mask_email(subscriber.email),
            "subscribed": subscriber.status == Subscriber.Status.SUBSCRIBED,
        }

    @extend_schema(
        summary="Subscription behind an unsubscribe link",
        responses={200: token_status_response},
    )
    def get(self, request, token):
        return success("S060", "Subscription retrieved", self._data(self._subscriber(token)))

    @extend_schema(
        summary="Unsubscribe (or resubscribe) from a link",
        request=SubscriptionSerializer,
        responses={200: token_status_response},
    )
    def post(self, request, token):
        subscriber = self._subscriber(token)

        # One-click requests carry no "subscribed" field: they mean "stop".
        subscribed = request.data.get("subscribed", False)
        subscribed = subscribed in {True, "true", "True", "1", 1}

        subscriber = services.set_subscribed(subscriber, subscribed)

        return success(
            "S061",
            "Subscribed" if subscribed else "Unsubscribed",
            self._data(subscriber),
        )
