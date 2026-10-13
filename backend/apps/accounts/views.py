from django.contrib.auth import update_session_auth_hash

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import MemberProfile, User
from .permissions import (
    IsAdminOrManager,
    IsStaffRole,
    IsSuperAdminOrAdmin,
)
from .serializers import (
    ChangePasswordSerializer,
    LoginDataSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    MemberAdminSerializer,
    MemberProfileSerializer,
    MeSerializer,
    RegistrationSerializer,
    UserSerializer,
)
from .services import (
    approve_member,
    create_member_profile,
    reactivate_member,
    reject_member,
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
        request=ChangePasswordSerializer,
        responses={200: None},
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

        return Response(
            {
                "success": True,
                "code": "S006",
                "description": "Password changed successfully.",
                "data": None,
            },
            status=status.HTTP_200_OK,
        )


# ============================================================================
# MEMBER MANAGEMENT
# ============================================================================

class MemberViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("-created_at")

    # We do not want staff creating arbitrary users through this endpoint.
    http_method_names = [
        "get",
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