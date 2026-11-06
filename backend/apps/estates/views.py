from django.db.models import Count
from django.utils.text import slugify
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import BasePermission

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffRole
from apps.common.responses import envelope, failure, success

from .models import Estate


ESTATE_ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.ADMIN,
}


class CanManageEstates(BasePermission):
    message = "Only Super Admins and Admins can add, edit or delete estates."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ESTATE_ADMIN_ROLES


class EstateSerializer(serializers.ModelSerializer):
    # Optional: made from the name when left out
    code = serializers.SlugField(max_length=50, required=False, allow_blank=True)
    plot_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Estate
        fields = (
            "id",
            "name",
            "code",
            "description",
            "location",
            "county",
            "is_active",
            "plot_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "plot_count", "created_at", "updated_at")
        # Uniqueness is checked in validate(), ignoring case.
        validators = []

    def _clash(self, **lookup):
        clash = Estate.objects.filter(**lookup)

        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)

        return clash.exists()

    def validate(self, attrs):
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()

            if self._clash(name__iexact=attrs["name"]):
                raise serializers.ValidationError({"name": "An estate with this name already exists."})

        if "code" in attrs:
            attrs["code"] = attrs["code"].strip().lower()

            if attrs["code"] and self._clash(code__iexact=attrs["code"]):
                raise serializers.ValidationError({"code": "Another estate already uses this code."})

        # A new estate without a code gets one from its name
        if self.instance is None and not attrs.get("code"):
            base = slugify(attrs.get("name", ""))[:44] or "estate"
            code, n = base, 2

            while self._clash(code__iexact=code):
                code, n = f"{base}-{n}", n + 1

            attrs["code"] = code

        if self.instance is not None and "code" in attrs and not attrs["code"]:
            raise serializers.ValidationError({"code": "The code can't be blank."})

        return attrs


estate_response = envelope("EstateResponse", EstateSerializer())


@extend_schema_view(
    list=extend_schema(
        summary="List estates",
        description="Staff only. Every estate, active ones first, then by name, with its number of plots.",
        responses={200: envelope("EstateListResponse", EstateSerializer(many=True))},
    ),
    retrieve=extend_schema(summary="Get an estate", responses={200: estate_response}),
    create=extend_schema(
        summary="Add an estate",
        description="Super Admins and Admins. `code` is made from the name when left out.",
        responses={201: estate_response},
    ),
    partial_update=extend_schema(summary="Edit an estate", responses={200: estate_response}),
    destroy=extend_schema(
        summary="Delete an estate",
        description=(
            "Only an estate nothing refers to (no plots, payments, posts, "
            "listings and so on). Otherwise deactivate it with `is_active`."
        ),
        responses={200: envelope("EmptyResponse")},
    ),
)
class EstateViewSet(viewsets.ModelViewSet):
    """
    The estates in the community. Staff read them; Super Admins and
    Admins add, edit, deactivate and delete them.
    """

    serializer_class = EstateSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    pagination_class = None

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaffRole()]

        return [CanManageEstates()]

    def get_queryset(self):
        return Estate.objects.annotate(plot_count=Count("properties")).order_by("-is_active", "name")

    def _data(self, estate):
        return EstateSerializer(self.get_queryset().get(pk=estate.pk)).data

    def list(self, request, *args, **kwargs):
        return success("S108", "Estates retrieved", EstateSerializer(self.get_queryset(), many=True).data)

    def retrieve(self, request, *args, **kwargs):
        return success("S109", "Estate retrieved", self._data(self.get_object()))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        estate = serializer.save()

        return success("S110", "Estate added", self._data(estate), status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        estate = serializer.save()

        return success("S111", "Estate updated", self._data(estate))

    def destroy(self, request, *args, **kwargs):
        estate = self.get_object()

        # Some relations cascade or null out on delete; never let removing an
        # estate take plots, payments or members' posts with it.
        in_use = any(
            getattr(estate, rel.get_accessor_name()).exists()
            for rel in estate._meta.related_objects
            if rel.one_to_many or rel.many_to_many
        )

        if in_use:
            return failure(
                "E027",
                "This estate has plots, payments or other records, so it can't be deleted. Deactivate it instead.",
                status.HTTP_409_CONFLICT,
            )

        estate.delete()

        return success("S112", "Estate deleted")
