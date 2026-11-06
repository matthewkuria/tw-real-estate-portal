from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import EstateViewSet


app_name = "estates"


router = SimpleRouter()

router.register(r"", EstateViewSet, basename="estate")


urlpatterns = [
    path("", include(router.urls)),
]
