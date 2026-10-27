from django.db import transaction
from django.db.models import Count, Q
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from apps.common.responses import (
    EnvelopeListMixin,
    envelope,
    failure,
    page_of,
    success,
)
from apps.notifications import events as notification_events

from . import services
from .access import (
    CanPostUpdates,
    IsAuthorOrModerator,
    IsModerator,
    is_moderator,
    visible_to,
)
from .models import Message, Thread, Update
from .serializers import (
    MessageEditSerializer,
    MessageSerializer,
    ReactionSerializer,
    ThreadSerializer,
    UpdateSerializer,
)


estate_filter = OpenApiParameter(
    "estate",
    int,
    description="Only items tagged with this estate.",
)


def filter_by_estate(queryset, request):
    estate = request.query_params.get("estate")

    if estate is None:
        return queryset

    if not estate.isdigit():
        raise ValidationError({"estate": "Must be an estate id."})

    return queryset.filter(estate_id=int(estate))


# ============================================================================
# UPDATES
# ============================================================================

@extend_schema_view(
    list=extend_schema(
        parameters=[estate_filter],
        responses={200: envelope("UpdateListResponse", page_of("UpdatePage", UpdateSerializer))},
    ),
    retrieve=extend_schema(
        responses={200: envelope("UpdateResponse", UpdateSerializer())},
    ),
    create=extend_schema(
        responses={201: envelope("UpdateResponse", UpdateSerializer())},
    ),
    partial_update=extend_schema(
        responses={200: envelope("UpdateResponse", UpdateSerializer())},
    ),
    destroy=extend_schema(
        responses={200: envelope("EmptyResponse")},
    ),
)
class UpdateViewSet(EnvelopeListMixin, viewsets.ModelViewSet):
    """
    Admin announcements. Everyone signed in can read the ones visible to
    them; only Super Admins and Admins can post, edit or delete.
    """

    serializer_class = UpdateSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S013"
    list_description = "Updates retrieved"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Update.objects.none()

        queryset = visible_to(
            Update.objects.select_related("author", "estate"),
            self.request.user,
        )
        return filter_by_estate(queryset, self.request)

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]

        return [IsAuthenticated(), CanPostUpdates()]

    def retrieve(self, request, *args, **kwargs):
        update = self.get_object()

        return success(
            "S014",
            "Update retrieved",
            self.get_serializer(update).data,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            update = serializer.save(author=request.user)
            services.announce_update(update, "update.created")
            notification_events.update_posted(update)

        return success(
            "S015",
            "Update posted",
            self.get_serializer(update).data,
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        update = self.get_object()
        previous_estate_id = update.estate_id

        serializer = self.get_serializer(update, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        update = serializer.save()
        services.announce_update(update, "update.updated", previous_estate_id)

        return success(
            "S016",
            "Update edited",
            self.get_serializer(update).data,
        )

    def destroy(self, request, *args, **kwargs):
        update = self.get_object()
        update_id, estate_id = update.pk, update.estate_id

        update.delete()
        services.announce_update_deleted(update_id, estate_id)

        return success("S017", "Update deleted")


# ============================================================================
# FORUM THREADS
# ============================================================================

thread_response = envelope("ThreadResponse", ThreadSerializer())


@extend_schema_view(
    list=extend_schema(
        parameters=[
            estate_filter,
            OpenApiParameter("search", str, description="Match in title."),
        ],
        responses={200: envelope("ThreadListResponse", page_of("ThreadPage", ThreadSerializer))},
    ),
    retrieve=extend_schema(responses={200: thread_response}),
    create=extend_schema(responses={201: thread_response}),
    partial_update=extend_schema(responses={200: thread_response}),
    destroy=extend_schema(responses={200: envelope("EmptyResponse")}),
)
class ThreadViewSet(
    EnvelopeListMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Forum topics. Any signed-in user can start a thread and post in the
    ones they can see. Authors can rename or delete their own threads;
    moderators (Super Admin, Admin, Manager) can do so for any thread and
    can pin or lock threads.
    """

    serializer_class = ThreadSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    list_code = "S018"
    list_description = "Threads retrieved"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Thread.objects.none()

        queryset = visible_to(
            Thread.objects
            .select_related("author", "estate")
            .annotate(
                message_count=Count(
                    "messages",
                    filter=Q(messages__is_deleted=False),
                ),
            )
            # Meta.ordering is dropped for aggregate queries; restate it.
            .order_by("-is_pinned", "-last_message_at", "-id"),
            self.request.user,
        )

        search = self.request.query_params.get("search")

        if search:
            queryset = queryset.filter(title__icontains=search)

        return filter_by_estate(queryset, self.request)

    def get_permissions(self):
        if self.action in {"partial_update", "update", "destroy"}:
            return [IsAuthenticated(), IsAuthorOrModerator()]

        if self.action in {"pin", "unpin", "lock", "unlock"}:
            return [IsAuthenticated(), IsModerator()]

        return [IsAuthenticated()]

    def get_throttles(self):
        if self.request.method == "POST" and self.action in {"create", "messages"}:
            self.throttle_scope = "forum_post"
            return [ScopedRateThrottle()]

        return []

    def get_serializer_context(self):
        context = super().get_serializer_context()

        if self.action == "messages" and "pk" in self.kwargs:
            context["thread"] = self.get_object()

        return context

    def _thread_data(self, thread):
        # Re-read through the queryset so message_count is annotated.
        return self.get_serializer(self.get_queryset().get(pk=thread.pk)).data

    def retrieve(self, request, *args, **kwargs):
        return success("S019", "Thread retrieved", self._thread_data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        thread = services.create_thread(
            author=request.user,
            title=serializer.validated_data["title"],
            body=serializer.validated_data["body"],
            estate=serializer.validated_data.get("estate"),
        )

        return success(
            "S020",
            "Thread created",
            self._thread_data(thread),
            status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        thread = self.get_object()
        serializer = self.get_serializer(thread, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        fields = {
            name: value
            for name, value in serializer.validated_data.items()
            if name == "title"
        }

        if fields:
            services.update_thread(thread, **fields)

        return success("S021", "Thread updated", self._thread_data(thread))

    def destroy(self, request, *args, **kwargs):
        services.delete_thread(self.get_object())

        return success("S022", "Thread deleted")

    # ------------------------------------------------------------------------
    # Moderation
    # ------------------------------------------------------------------------

    def _set_flag(self, code, description, **fields):
        thread = services.update_thread(self.get_object(), **fields)

        return success(code, description, self._thread_data(thread))

    @extend_schema(request=None, responses={200: thread_response})
    @action(detail=True, methods=["post"])
    def pin(self, request, pk=None):
        return self._set_flag("S023", "Thread pinned", is_pinned=True)

    @extend_schema(request=None, responses={200: thread_response})
    @action(detail=True, methods=["post"])
    def unpin(self, request, pk=None):
        return self._set_flag("S024", "Thread unpinned", is_pinned=False)

    @extend_schema(request=None, responses={200: thread_response})
    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        return self._set_flag("S025", "Thread locked", is_locked=True)

    @extend_schema(request=None, responses={200: thread_response})
    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        return self._set_flag("S026", "Thread unlocked", is_locked=False)

    # ------------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------------

    @extend_schema(
        methods=["GET"],
        summary="List messages in a thread",
        description=(
            "Returns the latest `limit` messages, oldest first. Pass "
            "`before=<id>` to load older history, or `after=<id>` to fetch "
            "anything newer than the last message the client has."
        ),
        parameters=[
            OpenApiParameter("before", int, description="Messages older than this id."),
            OpenApiParameter("after", int, description="Messages newer than this id."),
            OpenApiParameter("limit", int, description="Default 50, max 100."),
        ],
        responses={
            200: envelope(
                "MessageListResponse",
                inline_serializer(
                    name="MessagePage",
                    fields={
                        "results": MessageSerializer(many=True),
                        "has_more": serializers.BooleanField(),
                    },
                ),
            ),
        },
    )
    @extend_schema(
        methods=["POST"],
        summary="Post a message",
        request=MessageSerializer,
        responses={201: envelope("MessageResponse", MessageSerializer())},
    )
    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        thread = self.get_object()

        if request.method == "POST":
            return self._post_message(request, thread)

        return self._list_messages(request, thread)

    def _post_message(self, request, thread):
        serializer = MessageSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)

        try:
            message = services.post_message(
                thread,
                request.user,
                serializer.validated_data["body"],
                serializer.validated_data.get("reply_to"),
                as_moderator=is_moderator(request.user),
            )
        except services.ThreadLocked as exc:
            return failure("E008", str(exc), status.HTTP_403_FORBIDDEN)

        return success(
            "S028",
            "Message posted",
            MessageSerializer(message, context={"request": request}).data,
            status.HTTP_201_CREATED,
        )

    def _list_messages(self, request, thread):
        params = {}

        for name in ("before", "after", "limit"):
            value = request.query_params.get(name)

            if value is None:
                continue

            if not value.isdigit():
                raise ValidationError({name: "Must be a positive integer."})

            params[name] = int(value)

        limit = min(max(params.get("limit", 50), 1), 100)
        queryset = (
            thread.messages
            .select_related("author")
            .prefetch_related("reactions")
        )

        if "after" in params:
            page = list(
                queryset.filter(id__gt=params["after"]).order_by("id")[:limit + 1]
            )
            has_more = len(page) > limit
            page = page[:limit]
        else:
            if "before" in params:
                queryset = queryset.filter(id__lt=params["before"])

            page = list(queryset.order_by("-id")[:limit + 1])
            has_more = len(page) > limit
            page = page[:limit][::-1]

        return success(
            "S027",
            "Messages retrieved",
            {
                "results": MessageSerializer(
                    page,
                    many=True,
                    context={"request": request},
                ).data,
                "has_more": has_more,
            },
        )


# ============================================================================
# FORUM MESSAGES
# ============================================================================

message_response = envelope("MessageResponse", MessageSerializer())


@extend_schema_view(
    retrieve=extend_schema(responses={200: message_response}),
    destroy=extend_schema(responses={200: envelope("EmptyResponse")}),
)
class MessageViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    A single message. Authors can edit their own messages; authors and
    moderators can delete them. Anyone who can see a message can react.
    """

    serializer_class = MessageSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()

        threads = visible_to(Thread.objects.all(), self.request.user)

        return (
            Message.objects
            .filter(thread__in=threads)
            .select_related("author", "thread")
            .prefetch_related("reactions")
        )

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), IsAuthorOrModerator()]

        return [IsAuthenticated()]

    def _message_data(self, message):
        return MessageSerializer(message, context={"request": self.request}).data

    def retrieve(self, request, *args, **kwargs):
        return success("S029", "Message retrieved", self._message_data(self.get_object()))

    @extend_schema(request=MessageEditSerializer, responses={200: message_response})
    def partial_update(self, request, *args, **kwargs):
        message = self.get_object()

        if message.author_id != request.user.pk:
            return failure(
                "E009",
                "You can only edit your own messages.",
                status.HTTP_403_FORBIDDEN,
            )

        if message.is_deleted:
            return failure(
                "E010",
                "Deleted messages cannot be edited.",
                status.HTTP_400_BAD_REQUEST,
            )

        if message.thread.is_locked and not is_moderator(request.user):
            return failure("E008", "This thread is locked.", status.HTTP_403_FORBIDDEN)

        serializer = MessageEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = services.edit_message(message, serializer.validated_data["body"])

        return success("S030", "Message edited", self._message_data(message))

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        services.delete_message(self.get_object())

        return success("S031", "Message deleted")

    @extend_schema(
        summary="Toggle a reaction",
        description="Adds the reaction, or removes it if you already reacted with it.",
        request=ReactionSerializer,
        responses={200: message_response},
    )
    @action(detail=True, methods=["post"])
    def react(self, request, pk=None):
        message = self.get_object()

        if message.is_deleted:
            return failure(
                "E011",
                "You cannot react to a deleted message.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.toggle_reaction(
            message,
            request.user,
            serializer.validated_data["emoji"],
        )

        message = self.get_queryset().get(pk=message.pk)

        return success("S032", "Reaction updated", self._message_data(message))
