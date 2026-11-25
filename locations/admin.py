
from django.contrib import admin

from .models import Location


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "category",
        "state",
        "latitude",
        "longitude",
    )

    list_filter = (
        "state",
        "category",
    )

    search_fields = (
        "name",
        "state",
    )

    ordering = (
        "state",
        "name",
    )

    list_per_page = 25
