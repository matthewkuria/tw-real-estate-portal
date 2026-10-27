from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import AdvertisementViewSet


app_name = "advertisements"


router = SimpleRouter()

router.register(r"", AdvertisementViewSet, basename="ad")


urlpatterns = [
    path("", include(router.urls)),
]
