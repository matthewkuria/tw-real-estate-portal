from django.contrib.auth import update_session_auth_hash

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import MemberProfile, User
from .permissions import (
    IsAdminOrManager,
    IsStaffRole,
    IsSuperAdminOrAdmin,
)
from .serializers import (
    ChangePasswordResponseSerializer,
    ChangePasswordSerializer,
    LoginDataSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MemberAdminSerializer,
    MemberProfileSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegistrationSerializer,
    UserSerializer,
)
from .services import (
    approve_member,
    create_member_profile,
    reactivate_member,
    reject_member,
    reset_password,
    revoke_refresh_tokens,
    send_password_reset_email,
    suspend_member,
)


# ============================================================================
# AUTHENTICATION
# ============================================================================

class RegistrationView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=RegistrationSerializer,
        responses={201: UserSerializer},
    )
    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        create_member_profile(user)

        return Response(
            {
                "success": True,
                "code": "S001",
                "description": (
                    "Registration successful. "
                    "Your account is pending approval."
                ),
                "data": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=LoginSerializer,
        responses={200: LoginResponseSerializer},
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].strip().lower()
        password = serializer.validated_data["password"]

        user = User.objects.filter(email=email).first()

        # ------------------------------------------------------------------
        # Invalid credentials
        # ------------------------------------------------------------------

        if not user or not user.check_password(password):
            return Response(
                {
                    "success": False,
                    "code": "E001",
                    "description": "Invalid email or password.",
                    "data": None,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ------------------------------------------------------------------
        # Account status checks
        # ------------------------------------------------------------------

        if user.status == User.Status.PENDING:
            return Response(
                {
                    "success": False,
                    "code": "E002",
                    "description": "Your account is pending approval.",
                    "data": None,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if user.status == User.Status.REJECTED:
            return Response(
                {
                    "success": False,
                    "code": "E003",
                    "description": (
                        "Your account registration was rejected."
                    ),
                    "data": None,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if user.status == User.Status.SUSPENDED:
            return Response(
                {
                    "success": False,
                    "code": "E004",
                    "description": "Your account has been suspended.",
                    "data": None,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.is_active:
            return Response(
                {
                    "success": False,
                    "code": "E005",
                    "description": "Your account is inactive.",
                    "data": None,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # Generate JWT
        # ------------------------------------------------------------------

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "success": True,
                "code": "S002",
                "description": "User signed in",
                "data": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "user": UserSerializer(user).data,
                },
            },
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: MeSerializer},
    )
    def get(self, request):
        serializer = MeSerializer(request.user)

        return Response(
            {
                "success": True,
                "code": "S004",
                "description": "User profile retrieved",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=MeSerializer,
        responses={200: MeSerializer},
    )
    def patch(self, request):
        serializer = MeSerializer(
            request.user,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "success": True,
                "code": "S005",
                "description": "User profile updated",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class MyProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: MemberProfileSerializer},
    )
    def get(self, request):
        profile, _ = MemberProfile.objects.get_or_create(
            user=request.user
        )

        serializer = MemberProfileSerializer(profile)

        return Response(
            {
                "success": True,
                "code": "S004",
                "description": "Member profile retrieved",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=MemberProfileSerializer,
        responses={200: MemberProfileSerializer},
    )
    def patch(self, request):
        profile, _ = MemberProfile.objects.get_or_create(
            user=request.user
        )

        serializer = MemberProfileSerializer(
            profile,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "success": True,
                "code": "S005",
                "description": "Member profile updated",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Change password",
        description=(
            "Change the signed-in user's password.\n\n"
            "On success, every refresh token the user holds is revoked, "
            "signing them out on all other devices. A new token pair is "
            "returned for the current device. The client **must replace "
            "its stored tokens** with these, or it will be signed out at "
            "its next refresh.\n\n"
            "Access tokens already issued to other devices stay valid "
            "until they expire (`ACCESS_TOKEN_LIFETIME`, 30 minutes)."
        ),
        request=ChangePasswordSerializer,
        responses={
            200: ChangePasswordResponseSerializer,
            400: OpenApiResponse(
                description=(
                    "Validation failed: wrong current password, "
                    "confirmation mismatch, or a new password that fails "
                    "the password rules. Errors are keyed by field."
                ),
            ),
            401: OpenApiResponse(
                description="Missing, invalid or expired access token.",
            ),
        },
        examples=[
            OpenApiExample(
                "Request",
                request_only=True,
                value={
                    "current_password": "Old-pass-123!",
                    "new_password": "New-pass-456!",
                    "new_password_confirmation": "New-pass-456!",
                },
            ),
            OpenApiExample(
                "Success",
                response_only=True,
                status_codes=["200"],
                value={
                    "success": True,
                    "code": "S006",
                    "description": "Password changed successfully.",
                    "data": {
                        "access": "eyJhbGciOiJIUzI1NiIs...",
                        "refresh": "eyJhbGciOiJIUzI1NiIs...",
                    },
                },
            ),
            OpenApiExample(
                "Wrong current password",
                response_only=True,
                status_codes=["400"],
                value={
                    "current_password": ["Current password is incorrect."],
                },
            ),
            OpenApiExample(
                "Passwords do not match",
                response_only=True,
                status_codes=["400"],
                value={
                    "new_password_confirmation": ["Passwords do not match."],
                },
            ),
        ],
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )

        serializer.is_valid(raise_exception=True)

        user = request.user

        user.set_password(
            serializer.validated_data["new_password"]
        )

        user.save(update_fields=["password"])

        update_session_auth_hash(request, user)

        # Sign out every other device, then issue fresh tokens for this one.
        revoke_refresh_tokens(user)
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "success": True,
                "code": "S006",
                "description": "Password changed successfully.",
                "data": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=LogoutSerializer,
        responses={200: None},
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refresh = RefreshToken(serializer.validated_data["refresh"])

            if str(refresh[api_settings.USER_ID_CLAIM]) != str(request.user.pk):
                raise TokenError("Token does not belong to this user.")

            refresh.blacklist()
        except TokenError:
            return Response(
                {
                    "success": False,
                    "code": "E007",
                    "description": "Invalid or expired refresh token.",
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "code": "S003",
                "description": "User signed out",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={200: None},
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        send_password_reset_email(serializer.validated_data["email"])

        # Same response whether or not the email is registered.
        return Response(
            {
                "success": True,
                "code": "S011",
                "description": (
                    "If an account exists for this email, "
                    "a password reset link has been sent."
                ),
                "data": None,
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={200: None},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reset_password(
            serializer.validated_data["user"],
            serializer.validated_data["new_password"],
        )

        return Response(
            {
                "success": True,
                "code": "S012",
                "description": "Password has been reset. Please sign in.",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )


# ============================================================================
# MEMBER MANAGEMENT
# ============================================================================

class MemberViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = User.objects.all().order_by("-created_at")

    # No create or destroy mixins: staff must not create or delete users
    # here. POST is allowed only for the status actions below.
    http_method_names = [
        "get",
        "post",
        "patch",
        "head",
        "options",
    ]

    def get_serializer_class(self):
        if self.action in {
            "list",
            "retrieve",
            "update",
            "partial_update",
            "approve",
            "reject",
            "suspend",
            "reactivate",
        }:
            return MemberAdminSerializer

        return UserSerializer

    def get_permissions(self):
        if self.action in {
            "list",
            "retrieve",
        }:
            return [IsStaffRole()]

        if self.action in {
            "update",
            "partial_update",
        }:
            return [IsSuperAdminOrAdmin()]

        if self.action in {
            "approve",
            "reject",
            "suspend",
            "reactivate",
        }:
            return [IsAdminOrManager()]

        return [IsStaffRole()]

    # ------------------------------------------------------------------------
    # Approve
    # ------------------------------------------------------------------------

    @extend_schema(
        responses={200: MemberAdminSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="approve",
    )
    def approve(self, request, pk=None):
        user = self.get_object()

        try:
            user = approve_member(user)
        except ValueError as exc:
            return Response(
                {
                    "success": False,
                    "code": "E006",
                    "description": str(exc),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "code": "S007",
                "description": "Member approved successfully.",
                "data": MemberAdminSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------------
    # Reject
    # ------------------------------------------------------------------------

    @extend_schema(
        responses={200: MemberAdminSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="reject",
    )
    def reject(self, request, pk=None):
        user = self.get_object()

        try:
            user = reject_member(user)
        except ValueError as exc:
            return Response(
                {
                    "success": False,
                    "code": "E006",
                    "description": str(exc),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "code": "S008",
                "description": "Member rejected successfully.",
                "data": MemberAdminSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------------
    # Suspend
    # ------------------------------------------------------------------------

    @extend_schema(
        responses={200: MemberAdminSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="suspend",
    )
    def suspend(self, request, pk=None):
        user = self.get_object()

        try:
            user = suspend_member(user)
        except ValueError as exc:
            return Response(
                {
                    "success": False,
                    "code": "E006",
                    "description": str(exc),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "code": "S009",
                "description": "Member suspended successfully.",
                "data": MemberAdminSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------------
    # Reactivate
    # ------------------------------------------------------------------------

    @extend_schema(
        responses={200: MemberAdminSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="reactivate",
    )
    def reactivate(self, request, pk=None):
        user = self.get_object()

        try:
            user = reactivate_member(user)
        except ValueError as exc:
            return Response(
                {
                    "success": False,
                    "code": "E006",
                    "description": str(exc),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "code": "S010",
                "description": "Member reactivated successfully.",
                "data": MemberAdminSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


# ============================================================================
# MEMBER PROFILE MANAGEMENT
# ============================================================================

class MemberProfileViewSet(viewsets.ModelViewSet):
    queryset = MemberProfile.objects.select_related("user").all()
    serializer_class = MemberProfileSerializer

    # Profile records should not be created/deleted arbitrarily.
    http_method_names = [
        "get",
        "patch",
        "head",
        "options",
    ]

    def get_permissions(self):
        if self.request.method in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            return [IsStaffRole()]

        return [IsSuperAdminOrAdmin()]