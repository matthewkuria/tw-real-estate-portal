from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import NotificationPreferencesView, NotificationViewSet


app_name = "notifications"


router = SimpleRouter()

router.register(r"", NotificationViewSet, basename="notification")


urlpatterns = [
    path(
        "preferences/",
        NotificationPreferencesView.as_view(),
        name="preferences",
    ),

    path("", include(router.urls)),
]
