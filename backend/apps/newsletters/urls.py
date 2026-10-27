from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    ArchiveViewSet,
    NewsletterViewSet,
    SubscriptionView,
    UnsubscribeView,
)


app_name = "newsletters"


router = SimpleRouter()

router.register(r"issues", NewsletterViewSet, basename="issue")
router.register(r"archive", ArchiveViewSet, basename="archive")


urlpatterns = [
    path(
        "subscription/",
        SubscriptionView.as_view(),
        name="subscription",
    ),

    path(
        "unsubscribe/<uuid:token>/",
        UnsubscribeView.as_view(),
        name="unsubscribe",
    ),

    path("", include(router.urls)),
]
