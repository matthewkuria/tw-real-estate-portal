from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
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

# Uploaded media in development; production serves it from the web server.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
