from rest_framework import serializers


class FormBooleanField(serializers.BooleanField):
    """
    DRF reads a boolean missing from form data as False (HTML checkbox
    rules), so a multipart upload that leaves it out would silently set
    it to False. Treat "missing" as "not given" so the default applies.
    """

    default_empty_html = serializers.empty
