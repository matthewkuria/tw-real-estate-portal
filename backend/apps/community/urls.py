from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import MessageViewSet, ThreadViewSet, UpdateViewSet


app_name = "community"


router = SimpleRouter()

router.register(r"updates", UpdateViewSet, basename="update")
router.register(r"threads", ThreadViewSet, basename="thread")
router.register(r"messages", MessageViewSet, basename="message")


urlpatterns = [
    path("", include(router.urls)),
]
