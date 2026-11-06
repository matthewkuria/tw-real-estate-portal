from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import PaymentViewSet


app_name = "payments"


router = SimpleRouter()

router.register(r"", PaymentViewSet, basename="payment")


urlpatterns = [
    path("", include(router.urls)),
]
