from django.urls import include, path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ChangePasswordView,
    DirectoryView,
    LoginView,
    LogoutView,
    MemberProfileViewSet,
    MemberViewSet,
    MeView,
    MyProfileView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegistrationView,
)


app_name = "accounts"


router = SimpleRouter()

router.register(
    r"users",
    MemberViewSet,
    basename="user",
)

router.register(
    r"profiles",
    MemberProfileViewSet,
    basename="profile",
)


urlpatterns = [
    # =========================================================
    # Authentication
    # =========================================================

    path(
        "register/",
        RegistrationView.as_view(),
        name="register",
    ),

    path(
        "login/",
        LoginView.as_view(),
        name="login",
    ),

    path(
        "token/refresh/",
        TokenRefreshView.as_view(),
        name="token-refresh",
    ),

    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),

    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset",
    ),

    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),

    # =========================================================
    # Current user
    # =========================================================

    path(
        "me/",
        MeView.as_view(),
        name="me",
    ),

    path(
        "me/profile/",
        MyProfileView.as_view(),
        name="my-profile",
    ),

    path(
        "me/password/",
        ChangePasswordView.as_view(),
        name="change-password",
    ),

    # =========================================================
    # Member management
    # =========================================================

    path(
        "directory/",
        DirectoryView.as_view(),
        name="directory",
    ),

    path(
        "",
        include(router.urls),
    ),
]
