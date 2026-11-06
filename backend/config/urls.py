from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)


urlpatterns = [
    path("admin/", admin.site.urls),

    # Accounts API
    path(
        "api/v1/accounts/",
        include("apps.accounts.urls"),
    ),

    # Community updates and forum
    path(
        "api/v1/community/",
        include("apps.community.urls"),
    ),

    # Notifications
    path(
        "api/v1/notifications/",
        include("apps.notifications.urls"),
    ),

    # Public gallery
    path(
        "api/v1/gallery/",
        include("apps.gallery.urls"),
    ),

    # Newsletters
    path(
        "api/v1/newsletters/",
        include("apps.newsletters.urls"),
    ),

    # Advertisements
    path(
        "api/v1/ads/",
        include("apps.advertisements.urls"),
    ),

    # Service providers
    path(
        "api/v1/service-providers/",
        include("apps.service_providers.urls"),
    ),

    # Estates (read-only; managed in the Django admin)
    path(
        "api/v1/estates/",
        include("apps.estates.urls"),
    ),

    # Plots and their allocation to members
    path(
        "api/v1/properties/",
        include("apps.properties.urls"),
    ),

    # Payments toward estates and plots
    path(
        "api/v1/payments/",
        include("apps.payments.urls"),
    ),

    # Rented houses and tenants
    path(
        "api/v1/rentals/",
        include("apps.rentals.urls"),
    ),

    # Properties for sale or rent
    path(
        "api/v1/listings/",
        include("apps.listings.urls"),
    ),

    # OpenAPI schema
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),

    # Swagger UI
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),

    # ReDoc
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

# Uploaded media kept on local disk (development, or a mounted disk in
# production with SERVE_MEDIA on). Object storage serves its own files.
if settings.SERVE_MEDIA and settings.MEDIA_URL.startswith("/"):
    urlpatterns += [
        re_path(
            rf"^{settings.MEDIA_URL.strip('/')}/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
