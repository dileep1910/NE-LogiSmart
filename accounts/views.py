
import hashlib
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from .forms import RegisterForm
from .models import UserProfile


ADMIN_CODE_MAX_ATTEMPTS = 5
ADMIN_CODE_WINDOW_SECONDS = 15 * 60


# ==========================================
# ROLE AND DASHBOARD HELPERS
# ==========================================

def get_user_role(user):
    """
    Return the application role of a user.
    Missing profiles are treated as normal users.
    """
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        return UserProfile.ROLE_USER


def get_dashboard_url(user):
    """
    Return the appropriate dashboard URL.
    """
    if get_user_role(user) == UserProfile.ROLE_ADMIN:
        return "accounts:admin_dashboard"

    return "accounts:user_dashboard"


# ==========================================
# ADMIN REGISTRATION RATE LIMITING
# ==========================================

def get_client_ip(request):
    return request.META.get("REMOTE_ADDR", "unknown")


def get_admin_attempt_key(request):
    ip_address = get_client_ip(request)

    hashed_ip = hashlib.sha256(
        ip_address.encode("utf-8")
    ).hexdigest()

    return f"admin_registration_attempts_{hashed_ip}"


# ==========================================
# REGISTRATION
# ==========================================

def register(request):
    if request.user.is_authenticated:
        return redirect(get_dashboard_url(request.user))

    if request.method == "POST":
        form = RegisterForm(request.POST)

        if form.is_valid():
            selected_role = form.cleaned_data["role"]

            if selected_role == UserProfile.ROLE_ADMIN:
                attempt_key = get_admin_attempt_key(request)
                attempts = cache.get(attempt_key, 0)

                if attempts >= ADMIN_CODE_MAX_ATTEMPTS:
                    form.add_error(
                        "admin_code",
                        "Too many failed attempts. "
                        "Please try again after 15 minutes."
                    )

                else:
                    submitted_code = form.cleaned_data.get(
                        "admin_code", ""
                    )

                    configured_code = getattr(
                        settings,
                        "ADMIN_REGISTRATION_CODE",
                        None,
                    )

                    if not configured_code:
                        form.add_error(
                            None,
                            "Admin registration is temporarily unavailable."
                        )

                    elif not secrets.compare_digest(
                        submitted_code,
                        configured_code,
                    ):
                        cache.add(
                            attempt_key,
                            0,
                            timeout=ADMIN_CODE_WINDOW_SECONDS,
                        )
                        cache.incr(attempt_key)

                        form.add_error(
                            "admin_code",
                            "Invalid Admin registration code."
                        )

                    else:
                        cache.delete(attempt_key)

            if form.is_valid():
                with transaction.atomic():
                    user = form.save(commit=False)
                    user.save()

                    UserProfile.objects.create(
                        user=user,
                        role=selected_role,
                    )

                login(request, user)

                messages.success(
                    request,
                    "Your account has been created successfully!",
                )

                return redirect(get_dashboard_url(user))

    else:
        form = RegisterForm()

    return render(
        request,
        "accounts/register.html",
        {"form": form},
    )


# ==========================================
# LOGIN
# ==========================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect(get_dashboard_url(request.user))

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is not None:
            login(request, user)

            messages.success(
                request,
                f"Welcome back, {user.username}!",
            )

            return redirect(get_dashboard_url(user))

        messages.error(
            request,
            "Invalid username or password."
        )

    return render(request, "accounts/login.html")


# ==========================================
# USER DASHBOARD
# ==========================================

@login_required
def user_dashboard(request):

    if get_user_role(request.user) != UserProfile.ROLE_USER:
        messages.warning(
            request,
            "You do not have permission to access that dashboard."
        )
        return redirect(get_dashboard_url(request.user))

    return render(
        request,
        "accounts/user_dashboard.html",
    )


# ==========================================
# ADMIN DASHBOARD
# ==========================================

@login_required
def admin_dashboard(request):

    if get_user_role(request.user) != UserProfile.ROLE_ADMIN:
        messages.warning(
            request,
            "You do not have permission to access that dashboard."
        )
        return redirect(get_dashboard_url(request.user))

    return render(
        request,
        "accounts/admin_dashboard.html",
    )


# ==========================================
# LOGOUT
# ==========================================

@require_POST
def logout_view(request):
    logout(request)

    messages.success(
        request,
        "You have been logged out successfully."
    )

    return redirect("home")
