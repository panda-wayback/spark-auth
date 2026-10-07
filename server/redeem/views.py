import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.errors import ServiceError

from . import services


def _error(exc):
    return JsonResponse(
        {"ok": False, "error": {"code": exc.code, "message": exc.message}},
        status=400 if exc.code == "REQUEST_INVALID" else 403,
    )


@csrf_exempt
@require_POST
def redeem(request):
    try:
        try:
            data = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON")
        if not isinstance(data, dict):
            raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON 对象")
        code = data.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ServiceError("REQUEST_INVALID", "缺少参数 code")
        redemption = services.redeem(code.strip(), request.META.get("REMOTE_ADDR"))
    except ServiceError as exc:
        return _error(exc)
    return JsonResponse(
        {"ok": True, "remaining": redemption.remaining, "redeemed_at": redemption.redeemed_at.isoformat()}
    )
