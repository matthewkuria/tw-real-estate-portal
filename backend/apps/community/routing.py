from django.urls import path

from .consumers import CommunityConsumer


websocket_urlpatterns = [
    path("ws/community/", CommunityConsumer.as_asgi()),
]
