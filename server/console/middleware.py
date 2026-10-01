from django.http import HttpResponseBadRequest

from . import hosts


class AllowedHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hosts.is_allowed(hosts.request_domain(request)):
            return HttpResponseBadRequest(
                "该域名或 IP 未被允许访问，请用已允许的地址登录管理后台，在“访问地址”页添加。",
                content_type="text/plain; charset=utf-8",
            )
        return self.get_response(request)
