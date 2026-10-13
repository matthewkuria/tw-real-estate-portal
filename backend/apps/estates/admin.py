from django.contrib import admin

# Register your models here.
from .models import Estate
@admin.register(Estate)
class EstateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "location",
        "county",
        "is_active",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
        "code",
        "location",
        "county",
    )

    ordering = ("name",)