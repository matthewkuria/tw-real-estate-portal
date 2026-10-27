"""
The API's response envelope, shared by every app:

    {"success": bool, "code": str, "description": str, "data": ...}

Validation and permission errors keep DRF's default shape.
"""

from drf_spectacular.drainage import set_override
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


def success(code, description, data=None, status_code=status.HTTP_200_OK):
    return Response(
        {
            "success": True,
            "code": code,
            "description": description,
            "data": data,
        },
        status=status_code,
    )


def failure(code, description, status_code):
    return Response(
        {
            "success": False,
            "code": code,
            "description": description,
            "data": None,
        },
        status=status_code,
    )


def envelope(name, data=None):
    """
    OpenAPI schema for an enveloped response. `data` is a serializer
    instance, or None for responses without data.
    """
    schema = inline_serializer(
        name=name,
        fields={
            "success": serializers.BooleanField(),
            "code": serializers.CharField(),
            "description": serializers.CharField(),
            "data": data if data is not None else serializers.JSONField(allow_null=True),
        },
    )

    # The envelope is always one object, even for list actions, which
    # drf-spectacular would otherwise document as an array.
    set_override(schema, "many", False)

    return schema


def page_of(name, item_serializer, **extra_fields):
    """
    OpenAPI schema for the paginated list inside `data`.
    """
    return inline_serializer(
        name=name,
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": item_serializer(many=True),
            **extra_fields,
        },
    )


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class EnvelopeListMixin:
    """
    Paginated list wrapped in the envelope; `data` holds count, next,
    previous and results.

    The paginator is used directly rather than through pagination_class,
    so the API docs don't wrap the envelope in a second pagination layer.
    """

    list_code = None
    list_description = None

    def get_list_extra(self, queryset):
        """
        Extra keys to add to `data` alongside the page.
        """
        return {}

    def list(self, request, *args, **kwargs):
        paginator = StandardPagination()
        queryset = self.filter_queryset(self.get_queryset())
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = self.get_serializer(page, many=True)

        data = paginator.get_paginated_response(serializer.data).data
        data.update(self.get_list_extra(queryset))

        return success(self.list_code, self.list_description, data)
