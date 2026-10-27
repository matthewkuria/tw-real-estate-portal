from datetime import datetime

from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from .models import User, MemberProfile


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    password_confirmation = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    phone_number = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=20,
    )

    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "password_confirmation",
            "first_name",
            "last_name",
            "phone_number",
        )

    def validate_email(self, value):
        value = value.strip().lower()

        # The model's unique check runs before this lowercasing, so it
        # misses duplicates that differ only by case.
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )

        return value

    def validate(self, attrs):
        password = attrs.get("password")
        password_confirmation = attrs.pop("password_confirmation")

        if password != password_confirmation:
            raise serializers.ValidationError(
                {"password_confirmation": "Passwords do not match."}
            )

        try:
            password_validation.validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"password": list(exc.messages)}
            )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            role=User.Role.MEMBER,
            status=User.Status.PENDING,
            is_active=False,
        )

        MemberProfile.objects.create(
            user=user,
            phone_number=validated_data.get("phone_number", ""),
        )

        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "status",
            "approved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "role",
            "status",
            "approved_at",
            "created_at",
            "updated_at",
        )


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "status",
            "approved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "role",
            "status",
            "approved_at",
            "created_at",
            "updated_at",
        )

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    
class LoginDataSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class LoginResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    code = serializers.CharField()
    description = serializers.CharField()
    data = LoginDataSerializer()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class ChangePasswordResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    code = serializers.CharField()
    description = serializers.CharField()
    data = TokenPairSerializer()


class MemberProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberProfile
        fields = (
            "phone_number",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
        )


class MemberAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "status",
            "is_active",
            "approved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "status",
            "is_active",
            "approved_at",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        editor = self.context["request"].user
        target = self.instance
        is_super_admin = editor.role == User.Role.SUPER_ADMIN

        if (
            target is not None
            and target.role == User.Role.SUPER_ADMIN
            and not is_super_admin
        ):
            raise PermissionDenied(
                "Only Super Admins can edit a Super Admin account."
            )

        new_role = attrs.get("role")

        if target is not None and new_role and new_role != target.role:
            if not is_super_admin:
                raise PermissionDenied(
                    "Only Super Admins can change roles."
                )

            if target.pk == editor.pk:
                raise PermissionDenied(
                    "You cannot change your own role."
                )

        return attrs


class DirectoryUnitSerializer(serializers.Serializer):
    property = serializers.IntegerField(source="pk")
    estate = serializers.IntegerField(source="estate_id")
    estate_name = serializers.CharField(source="estate.name")
    plot_number = serializers.CharField()


class DirectoryEntrySerializer(serializers.ModelSerializer):
    """
    One person in the staff-only resident directory.
    """

    name = serializers.SerializerMethodField()
    phone_number = serializers.SerializerMethodField()
    role_label = serializers.CharField(source="get_role_display")
    is_staff_member = serializers.SerializerMethodField()
    member_since = serializers.SerializerMethodField()
    units = DirectoryUnitSerializer(source="properties", many=True)

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "role",
            "role_label",
            "is_staff_member",
            "member_since",
            "units",
        )

    def get_name(self, obj) -> str:
        return obj.get_full_name() or obj.email

    def get_phone_number(self, obj) -> str:
        profile = getattr(obj, "member_profile", None)
        return profile.phone_number if profile else ""

    def get_is_staff_member(self, obj) -> bool:
        return obj.role != User.Role.MEMBER

    def get_member_since(self, obj) -> datetime:
        # Members count from approval; staff (never "approved") from
        # account creation.
        return obj.approved_at or obj.created_at


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()

    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    new_password_confirmation = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate(self, attrs):
        try:
            user_id = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        if user is None or not default_token_generator.check_token(
            user,
            attrs["token"],
        ):
            raise serializers.ValidationError(
                {"token": "This password reset link is invalid or has expired."}
            )

        if attrs["new_password"] != attrs["new_password_confirmation"]:
            raise serializers.ValidationError(
                {
                    "new_password_confirmation":
                        "Passwords do not match."
                }
            )

        try:
            password_validation.validate_password(
                attrs["new_password"],
                user,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages)}
            )

        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    new_password_confirmation = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate_current_password(self, value):
        user = self.context["request"].user

        if not user.check_password(value):
            raise serializers.ValidationError(
                "Current password is incorrect."
            )

        return value

    def validate(self, attrs):
        new_password = attrs.get("new_password")
        confirmation = attrs.get("new_password_confirmation")

        if new_password != confirmation:
            raise serializers.ValidationError(
                {
                    "new_password_confirmation":
                        "Passwords do not match."
                }
            )

        user = self.context["request"].user

        try:
            password_validation.validate_password(
                new_password,
                user,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages)}
            )

        return attrs