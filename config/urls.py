from django.contrib import admin
from django.urls import path, include
from . import views

urlpatterns = [
    path("admin/", admin.site.urls),

    # Homepage
    path("", views.home, name="home"),

    # Accounts (register, login, logout)
    path("accounts/", include("accounts.urls")),

    # Logistics
    path("logistics/", include("logistics.urls")),
]