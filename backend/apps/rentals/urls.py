from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import RentalHouseViewSet, TenantViewSet


app_name = "rentals"


router = SimpleRouter()

router.register(r"houses", RentalHouseViewSet, basename="house")
router.register(r"tenants", TenantViewSet, basename="tenant")


urlpatterns = [
    path("", include(router.urls)),
]
