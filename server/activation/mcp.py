import json

import yaml
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
TOOL_NAME = "get_activation_guide"
INSTRUCTIONS = f"编写需要接入 Spark Auth 卡密激活的软件时，先调用 {TOOL_NAME} 获取服务地址、接口与接入规范。"
TOOL = {
    "name": TOOL_NAME,
    "description": "获取 Spark Auth 激活服务的接入说明：服务地址、激活/校验接口、请求响应与错误码、设备指纹采集规范。编写接入激活的客户端代码前调用。",
    "inputSchema": {"type": "object", "properties": {}},
}
DOCS = (
    ("客户端激活接入规范", "docs/client/activation/README.md"),
    ("客户端设备指纹规范", "docs/client/fingerprint/README.md"),
)
INTERFACE = "server/activation/interface.yaml"
CLIENT_API_PREFIX = "POST /api/"


def base_url(request):
    return request.build_absolute_uri("/").rstrip("/")


def client_interface(root):
    spec = yaml.safe_load((root / INTERFACE).read_text(encoding="utf-8"))
    picked = {
        key: [item for item in spec.get(key) or [] if item["name"].startswith(CLIENT_API_PREFIX)]
        for key in ("input", "output")
    }
    picked["errors"] = spec.get("errors") or []
    return yaml.safe_dump(picked, allow_unicode=True, sort_keys=False, width=1000)


def activation_guide(request):
    url = base_url(request)
    parts = [
        "# Spark Auth 激活接入说明",
        f"服务地址：{url}\n\n- 激活：POST {url}/api/activate\n- 校验：POST {url}/api/verify",
    ]
    root = settings.BASE_DIR.parent
    for title, relative in DOCS:
        content = (root / relative).read_text(encoding="utf-8")
        parts.append(f"## {title}（{relative}）\n\n{content}")
    parts.append(f"## 激活与校验接口（{INTERFACE}）\n\n```yaml\n{client_interface(root)}```")
    return "\n\n".join(parts)


def _result(request_id, result):
    return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id, code, message):
    return JsonResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _initialize(params):
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "spark-auth", "version": "1.0.0"},
        "instructions": INSTRUCTIONS,
    }


@csrf_exempt
@require_POST
def endpoint(request):
    try:
        message = json.loads(request.body or b"")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(None, -32700, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(None, -32600, "Invalid Request")
    if "id" not in message:
        return HttpResponse(status=202)

    request_id, method = message["id"], message["method"]
    params = message.get("params") or {}
    if method == "initialize":
        return _result(request_id, _initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [TOOL]})
    if method == "tools/call":
        if params.get("name") != TOOL_NAME:
            return _error(request_id, -32602, f"Unknown tool: {params.get('name')}")
        text = activation_guide(request)
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
    return _error(request_id, -32601, f"Method not found: {method}")
