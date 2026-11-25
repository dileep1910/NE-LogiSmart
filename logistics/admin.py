
from django.contrib import admin
from .models import Route


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):

    list_display = (
        "source",
        "destination",
        "transport_type",
        "distance_km",
        "estimated_time_hours",
        "estimated_cost",
        "reliability_score",
        "accessibility_score",
    )

    list_filter = ("transport_type", "source", "destination")

    search_fields = ("source", "destination")

    ordering = ("source", "destination")
