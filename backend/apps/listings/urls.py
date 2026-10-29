from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import PropertyListingViewSet


app_name = "listings"


router = SimpleRouter()

router.register(r"", PropertyListingViewSet, basename="listing")


urlpatterns = [
    path("", include(router.urls)),
]
