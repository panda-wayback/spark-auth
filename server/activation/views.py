import json
import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.errors import ServiceError

from . import services

_DEVICE_HASH = re.compile(r"^[0-9a-f]{64}$")
_DEVICE_INFO_MAX = 255

_STATUS = {
    "REQUEST_INVALID": 400,
}


def _error(exc):
    return JsonResponse(
        {"ok": False, "error": {"code": exc.code, "message": exc.message}},
        status=_STATUS.get(exc.code, 403),
    )


def _parse(request, required):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON")
    if not isinstance(data, dict):
        raise ServiceError("REQUEST_INVALID", "请求体必须是 JSON 对象")
    for field in required:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ServiceError("REQUEST_INVALID", f"缺少参数 {field}")
    if not _DEVICE_HASH.match(data["device_hash"]):
        raise ServiceError("REQUEST_INVALID", "device_hash 必须是 64 位小写十六进制")
    device_info = data.get("device_info", "")
    if not isinstance(device_info, str) or len(device_info) > _DEVICE_INFO_MAX:
        raise ServiceError("REQUEST_INVALID", f"device_info 必须是不超过 {_DEVICE_INFO_MAX} 个字符的字符串")
    return data


@csrf_exempt
@require_POST
def activate(request):
    try:
        data = _parse(request, ("code", "device_hash"))
        token, expires_at = services.activate(
            data["code"].strip(),
            data["device_hash"],
            data.get("device_info", ""),
            request.META.get("REMOTE_ADDR"),
        )
    except ServiceError as exc:
        return _error(exc)
    return JsonResponse({"ok": True, "token": token, "expires_at": expires_at.isoformat()})


@csrf_exempt
@require_POST
def verify(request):
    try:
        data = _parse(request, ("device_hash", "token"))
        expires_at = services.verify(data["device_hash"], data["token"])
    except ServiceError as exc:
        return _error(exc)
    return JsonResponse({"ok": True, "valid": True, "expires_at": expires_at.isoformat()})
