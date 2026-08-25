from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .models import LoginEvent


def request_ip_address(request):
    if request is None:
        return None

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None

    return request.META.get("REMOTE_ADDR") or None


def request_user_agent(request):
    if request is None:
        return ""

    return request.META.get("HTTP_USER_AGENT", "")


def credential_username(credentials):
    username = credentials.get("username") or credentials.get("email") or ""

    if username:
        return str(username)

    for key, value in credentials.items():
        if key != "password" and value:
            return str(value)

    return ""


def create_login_event(event_type, request, user=None, username=""):
    if user is not None and hasattr(user, "is_authenticated"):
        if not user.is_authenticated:
            user = None

    if user is not None and not username:
        username = user.get_username()

    LoginEvent.objects.create(
        event_type=event_type,
        user=user,
        username=username,
        ip_address=request_ip_address(request),
        user_agent=request_user_agent(request),
    )


@receiver(user_logged_in, dispatch_uid="lab_login_event_logged_in")
def record_login(sender, request, user, **kwargs):
    create_login_event(LoginEvent.LOGIN, request, user=user)


@receiver(user_logged_out, dispatch_uid="lab_login_event_logged_out")
def record_logout(sender, request, user, **kwargs):
    create_login_event(LoginEvent.LOGOUT, request, user=user)


@receiver(user_login_failed, dispatch_uid="lab_login_event_login_failed")
def record_failed_login(sender, credentials, request, **kwargs):
    create_login_event(
        LoginEvent.FAILED,
        request,
        username=credential_username(credentials),
    )
