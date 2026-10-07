from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import reverse


class SetupMiddleware:
    """系统中没有任何管理员时，除初始化页外的请求一律跳转到初始化页。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _has_admin() and request.path != reverse("console:setup"):
            return redirect("console:setup")
        return self.get_response(request)


def _has_admin():
    return get_user_model().objects.filter(is_superuser=True).exists()
