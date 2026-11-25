
from django.urls import path
from . import views

urlpatterns = [
    path("route-planner/", views.route_planner, name="route_planner"),
]
