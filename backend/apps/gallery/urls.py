from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import GalleryImageViewSet


app_name = "gallery"


router = SimpleRouter()

router.register(r"images", GalleryImageViewSet, basename="image")


urlpatterns = [
    path("", include(router.urls)),
]
