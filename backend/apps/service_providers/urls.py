from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import ServiceProviderViewSet


app_name = "service_providers"


router = SimpleRouter()

router.register(r"", ServiceProviderViewSet, basename="provider")


urlpatterns = [
    path("", include(router.urls)),
]
