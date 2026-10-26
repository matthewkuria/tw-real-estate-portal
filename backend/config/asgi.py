"""
ASGI config for config project.

Serves HTTP through Django and WebSockets through Channels.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Set up Django before importing anything that touches models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import OriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402

from apps.accounts.ws_auth import JWTAuthMiddleware  # noqa: E402
from apps.community.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": OriginValidator(
            JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
            settings.WEBSOCKET_ALLOWED_ORIGINS,
        ),
    }
)
