from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    LoginView,
    ChangePasswordView,
    MemberProfileViewSet,
    MemberViewSet,
    MeView,
    MyProfileView,
    RegistrationView,
)


app_name = "accounts"


# DRF API routes
router = DefaultRouter()

router.register(
    r"users",
    MemberViewSet,
    basename="member",
)

router.register(
    r"profiles",
    MemberProfileViewSet,
    basename="member-profile",
)


urlpatterns = [
    # =========================================================
    # Django Web Authentication
    # =========================================================

    path("login/", LoginView.as_view(), name="login"),

    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="accounts:login"
        ),
        name="logout",
    ),

    path(
        "password-change/",
        auth_views.PasswordChangeView.as_view(
            template_name="accounts/password_change.html"
        ),
        name="password_change",
    ),

    path(
        "password-change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html"
        ),
        name="password_change_done",
    ),

    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            email_template_name="accounts/password_reset_email.html",
        ),
        name="password_reset",
    ),

    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),

    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html"
        ),
        name="password_reset_confirm",
    ),

    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),

    # =========================================================
    # DRF API
    # =========================================================

    path(
        "register/",
        RegistrationView.as_view(),
        name="register",
    ),

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

    path(
        "",
        include(router.urls),
    ),
]