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


def _parse_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON")
    if not isinstance(data, dict):
        raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON 对象")
    return data


def _require_code(data):
    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ServiceError("REQUEST_INVALID", "缺少参数 code")
    return code.strip()


@csrf_exempt
@require_POST
def redeem_status(request):
    try:
        data = _parse_body(request)
        code = _require_code(data)
        info = services.status(code)
    except ServiceError as exc:
        return _error(exc)
    return JsonResponse({"ok": True, "uses": info.uses, "used": info.used, "remaining": info.remaining})


@csrf_exempt
@require_POST
def redeem(request):
    try:
        data = _parse_body(request)
        code = _require_code(data)
        if "count" in data:
            redemption = services.redeem(code, count=data["count"], ip=request.META.get("REMOTE_ADDR"))
        else:
            redemption = services.redeem(code, ip=request.META.get("REMOTE_ADDR"))
    except ServiceError as exc:
        return _error(exc)
    return JsonResponse(
        {"ok": True, "remaining": redemption.remaining, "redeemed_at": redemption.redeemed_at.isoformat()}
    )
