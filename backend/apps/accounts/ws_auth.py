"""
JWT authentication for WebSocket connections.

Browsers can't set headers on a WebSocket, so the client passes its
access token in the query string: ws://host/ws/community/?token=<access>
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


@database_sync_to_async
def get_user_for_token(raw_token):
    authentication = JWTAuthentication()

    try:
        validated_token = authentication.get_validated_token(raw_token)
        return authentication.get_user(validated_token)
    except (InvalidToken, TokenError, AuthenticationFailed):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        token = query.get("token", [None])[0]

        scope["user"] = (
            await get_user_for_token(token) if token else AnonymousUser()
        )

        return await super().__call__(scope, receive, send)
