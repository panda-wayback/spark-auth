import ipaddress


class ForwardedForMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[-1].strip()
        try:
            request.META["REMOTE_ADDR"] = str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
        return self.get_response(request)
