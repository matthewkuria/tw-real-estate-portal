from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission

from apps.accounts.models import User
from apps.common.files import delete_files_after_commit
from apps.common.responses import EnvelopeListMixin, envelope, page_of, success
from apps.community.access import member_estate_ids, sees_all_estates

from .models import Advertisement
from .serializers import AdvertisementSerializer


AD_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


def is_ad_admin(user):
    return user.is_authenticated and user.role in AD_ADMIN_ROLES


class CanManageAds(BasePermission):
    message = "Only Super Admins and Admins can manage advertisements."

    def has_permission(self, request, view):
        return is_ad_admin(request.user)


def visible_to(queryset, user):
    """
    Live ads the viewer may see. Public ads are for everyone; members-only
    ads need a signed-in active account, and an estate on a members-only
    ad limits it to that estate's plot holders (staff see all).
    """
    now = timezone.now()

    queryset = queryset.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        is_published=True,
        publish_at__lte=now,
    )

    public = Q(visibility=Advertisement.Visibility.PUBLIC)

    if not user.is_authenticated or user.status != User.Status.ACTIVE:
        return queryset.filter(public)

    members = Q(visibility=Advertisement.Visibility.MEMBERS)

    if sees_all_estates(user):
        return queryset.filter(public | members)

    return queryset.filter(
        public
        | (members & Q(estate__isnull=True))
        | (members & Q(estate_id__in=member_estate_ids(user)))
    )


ADMIN_STATES = {"DRAFT", "SCHEDULED", "LIVE", "EXPIRED"}

ORDERINGS = {
    # Featured first, then newest.
    "newest": ("-is_featured", "-publish_at", "-id"),
    # Soonest event first; for listing upcoming events.
    "event_date": ("event_starts_at", "id"),
}


ad_response = envelope("AdvertisementResponse", AdvertisementSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List advertisements",
        description=(
            "Public. Anonymous visitors (the website) get live public ads. "
            "Signed-in members also get members-only ads meant for them. "
            "Admins get every ad and can filter by `state`."
        ),
        parameters=[
            OpenApiParameter("category", str, enum=Advertisement.Category.values),
            OpenApiParameter("estate", int, description="Only ads tagged with this estate."),
            OpenApiParameter("featured", bool, description="Only featured ads."),
            OpenApiParameter(
                "upcoming",
                bool,
                description="Only events that haven't started yet.",
            ),
            OpenApiParameter("search", str, description="Match in title, summary or description."),
            OpenApiParameter(
                "state",
                str,
                enum=sorted(ADMIN_STATES),
                description="Admins only.",
            ),
            OpenApiParameter("ordering", str, enum=list(ORDERINGS), description="Default: newest."),
        ],
        responses={
            200: envelope(
                "AdvertisementListResponse",
                page_of("AdvertisementPage", AdvertisementSerializer),
            ),
        },
    ),
    retrieve=extend_schema(summary="Get an advertisement", responses={200: ad_response}),
    create=extend_schema(summary="Post an advertisement", responses={201: ad_response}),
    partial_update=extend_schema(summary="Edit an advertisement", responses={200: ad_response}),
    destroy=extend_schema(
        summary="Delete an advertisement",
        responses={200: envelope("EmptyResponse")},
    ),
)
class AdvertisementViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Ads for events, services and items. Super Admins and Admins post and
    manage them; everyone else only reads the live ones they may see.
    """

    serializer_class = AdvertisementSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S062"
    list_description = "Advertisements retrieved"

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]

        return [CanManageAds()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Advertisement.objects.none()

        user = self.request.user
        params = self.request.query_params
        now = timezone.now()

        queryset = Advertisement.objects.select_related("estate")

        if is_ad_admin(user):
            state = params.get("state")

            if state is not None:
                if state not in ADMIN_STATES:
                    raise ValidationError({"state": "Unknown state."})

                queryset = queryset.filter(
                    {
                        "DRAFT": Q(is_published=False),
                        "SCHEDULED": Q(is_published=True, publish_at__gt=now),
                        "LIVE": Q(is_published=True, publish_at__lte=now)
                        & (Q(expires_at__isnull=True) | Q(expires_at__gt=now)),
                        "EXPIRED": Q(is_published=True, expires_at__lte=now),
                    }[state]
                )
        else:
            queryset = visible_to(queryset, user)

        category = params.get("category")

        if category is not None:
            if category not in Advertisement.Category.values:
                raise ValidationError({"category": "Unknown category."})

            queryset = queryset.filter(category=category)

        estate = params.get("estate")

        if estate is not None:
            if not estate.isdigit():
                raise ValidationError({"estate": "Must be an estate id."})

            queryset = queryset.filter(estate_id=int(estate))

        if params.get("featured") in {"true", "1"}:
            queryset = queryset.filter(is_featured=True)

        if params.get("upcoming") in {"true", "1"}:
            queryset = queryset.filter(
                category=Advertisement.Category.EVENT,
                event_starts_at__gt=now,
            )

        search = params.get("search")

        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(summary__icontains=search)
                | Q(description__icontains=search)
            )

        ordering = params.get("ordering", "newest")

        if ordering not in ORDERINGS:
            raise ValidationError({"ordering": f"Must be one of: {', '.join(ORDERINGS)}."})

        return queryset.order_by(*ORDERINGS[ordering])

    def retrieve(self, request, *args, **kwargs):
        return success(
            "S063",
            "Advertisement retrieved",
            self.get_serializer(self.get_object()).data,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ad = serializer.save(created_by=request.user)

        return success(
            "S064",
            "Advertisement posted",
            self.get_serializer(ad).data,
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        ad = self.get_object()
        old_files = (ad.image, ad.thumbnail)

        serializer = self.get_serializer(ad, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        image_changed = (
            serializer.validated_data.get("image")
            or serializer.validated_data.get("remove_image")
        )

        with transaction.atomic():
            ad = serializer.save()

            if image_changed:
                delete_files_after_commit(*old_files)

        return success("S065", "Advertisement updated", self.get_serializer(ad).data)

    def destroy(self, request, *args, **kwargs):
        ad = self.get_object()

        with transaction.atomic():
            delete_files_after_commit(ad.image, ad.thumbnail)
            ad.delete()

        return success("S066", "Advertisement deleted")
