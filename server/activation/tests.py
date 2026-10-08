import json
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from keys import services as keys_services
from keys.errors import ServiceError

from . import services
from .models import Activation, TransferLog

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64
INTERFACE_RELATIVE = "server/activation/interface.yaml"
INTERFACE_PATH = Path(__file__).resolve().parent / "interface.yaml"
REDEEM_INTERFACE_RELATIVE = "server/redeem/interface.yaml"
REDEEM_INTERFACE_PATH = settings.BASE_DIR.parent / REDEEM_INTERFACE_RELATIVE


def set_policy(product, allow_transfer=None, penalty=None, disabled=None):
    keys_services.update_product(
        product.id,
        product.name,
        product.allow_transfer if allow_transfer is None else allow_transfer,
        product.transfer_penalty_hours if penalty is None else penalty,
        product.disabled if disabled is None else disabled,
    )


class ActivationTestCase(TestCase):
    def setUp(self):
        self.product = keys_services.create_product("software-a", "A", True, 24)
        self.batch, (self.code,) = keys_services.issue_codes(self.product.id, 30, 1)

    def assertCode(self, code, func, *args):
        with self.assertRaises(ServiceError) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.code, code)

    def activation(self):
        return Activation.objects.get()


class ActivationServiceTests(ActivationTestCase):
    def test_first_activation_sets_expiry_without_log(self):
        before = timezone.now()
        _, expires_at = services.activate(self.code, DEVICE_A, "pc-a", "1.1.1.1")
        self.assertGreaterEqual(expires_at, before + timedelta(days=30))
        self.assertLessEqual(expires_at, timezone.now() + timedelta(days=30))
        (info,) = services.search("software-a")
        self.assertEqual((info.device_hash, info.ip), (DEVICE_A, "1.1.1.1"))
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_unactivated_code_does_not_expire(self):
        later = timezone.now() + timedelta(days=400)
        with patch("django.utils.timezone.now", return_value=later):
            _, expires_at = services.activate(self.code, DEVICE_A)
        self.assertEqual(expires_at, later + timedelta(days=30))

    def test_same_device_keeps_expiry(self):
        _, first = services.activate(self.code, DEVICE_A, ip="1.1.1.1")
        _, second = services.activate(self.code, DEVICE_A, ip="2.2.2.2")
        self.assertEqual(first, second)
        self.assertEqual(TransferLog.objects.count(), 0)
        (info,) = services.search("software-a")
        self.assertEqual(info.ip, "1.1.1.1")

    def test_transfer_not_allowed(self):
        set_policy(self.product, allow_transfer=False)
        services.activate(self.code, DEVICE_A)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, self.code, DEVICE_B)
        self.assertEqual(self.activation().device_hash, DEVICE_A)

    def test_transfer_deducts_and_logs(self):
        _, before = services.activate(self.code, DEVICE_A, "pc-a", "1.1.1.1")
        _, after = services.activate(self.code, DEVICE_B, "pc-b", "2001:db8::1")
        self.assertEqual(before - after, timedelta(hours=24))
        activation, (log,) = services.get_detail(self.activation().id)
        self.assertEqual(
            (log.old_device_hash, log.old_device_info, log.new_device_hash, log.new_device_info, log.new_ip),
            (DEVICE_A, "pc-a", DEVICE_B, "pc-b", "2001:db8::1"),
        )
        self.assertEqual((log.penalty_hours, log.expires_before, log.expires_after), (24, before, after))
        self.assertEqual((activation.device_hash, activation.ip), (DEVICE_B, "2001:db8::1"))

    def test_transfer_with_zero_penalty(self):
        set_policy(self.product, penalty=0)
        _, before = services.activate(self.code, DEVICE_A)
        _, after = services.activate(self.code, DEVICE_B)
        self.assertEqual(before, after)
        self.assertEqual(TransferLog.objects.count(), 1)

    def test_transfer_insufficient_time_changes_nothing(self):
        services.activate(self.code, DEVICE_A)
        almost = timezone.now() + timedelta(hours=10)
        Activation.objects.update(expires_at=almost)
        self.assertCode("TRANSFER_INSUFFICIENT_TIME", services.activate, self.code, DEVICE_B)
        activation = self.activation()
        self.assertEqual((activation.expires_at, activation.device_hash), (almost, DEVICE_A))
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_transfer_log_append_only(self):
        services.activate(self.code, DEVICE_A)
        services.activate(self.code, DEVICE_B)
        log = TransferLog.objects.get()
        with self.assertRaises(PermissionError):
            log.save()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_old_device_fails_after_transfer(self):
        token_a, _ = services.activate(self.code, DEVICE_A)
        services.activate(self.code, DEVICE_B)
        self.assertCode("DEVICE_MISMATCH", services.verify, DEVICE_A, token_a)

    def test_verify_success(self):
        token, expires_at = services.activate(self.code, DEVICE_A)
        self.assertEqual(services.verify(DEVICE_A, token), expires_at)

    def test_verify_token_used_on_other_device(self):
        token, _ = services.activate(self.code, DEVICE_A)
        self.assertCode("DEVICE_MISMATCH", services.verify, DEVICE_B, token)

    def test_tampered_token(self):
        token, _ = services.activate(self.code, DEVICE_A)
        self.assertCode("TOKEN_INVALID", services.verify, DEVICE_A, token[:-2] + "xx")
        self.assertCode("TOKEN_INVALID", services.verify, DEVICE_A, "garbage")

    def test_code_checks(self):
        plain = self.code.replace("-", "")
        tampered = plain[:20] + ("A" if plain[20] != "A" else "B") + plain[21:]
        self.assertCode("CODE_INVALID", services.activate, "NOPE", DEVICE_A)
        self.assertCode("CODE_INVALID", services.activate, tampered, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_count_code_rejected(self):
        product = keys_services.create_product("service-b", "B", kind="count")
        _, (code,) = keys_services.issue_codes(product.id, None, 1, uses=3)
        self.assertCode("CODE_TYPE_MISMATCH", services.activate, code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_disabled_and_expired(self):
        token, _ = services.activate(self.code, DEVICE_A)

        Activation.objects.update(disabled=True)
        self.assertCode("CODE_DISABLED", services.verify, DEVICE_A, token)
        self.assertCode("CODE_DISABLED", services.activate, self.code, DEVICE_A)
        Activation.objects.update(disabled=False)

        set_policy(self.product, disabled=True)
        self.assertCode("PRODUCT_DISABLED", services.verify, DEVICE_A, token)
        set_policy(self.product, disabled=False)

        Activation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertCode("CODE_EXPIRED", services.verify, DEVICE_A, token)
        self.assertCode("CODE_EXPIRED", services.activate, self.code, DEVICE_A)

    def test_batch_disabled_blocks_new_activation(self):
        keys_services.disable_batch(self.batch.batch_id)
        self.assertCode("BATCH_DISABLED", services.activate, self.code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_batch_disabled_keeps_activated(self):
        services.activate(self.code, DEVICE_A)
        keys_services.disable_batch(self.batch.batch_id)
        token, expires_at = services.activate(self.code, DEVICE_B)
        self.assertEqual(services.verify(DEVICE_B, token), expires_at)

    def test_formatted_input_matches_same_activation(self):
        services.activate(self.code, DEVICE_A)
        messy = " " + self.code.replace("-", "").lower()
        services.activate(messy, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 1)

    def test_policy_change_applies_immediately(self):
        services.activate(self.code, DEVICE_A)
        set_policy(self.product, allow_transfer=False)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, self.code, DEVICE_B)


class AdminQueryTests(ActivationTestCase):
    def test_count_and_search(self):
        other = keys_services.create_product("software-b", "B")
        _, codes = keys_services.issue_codes(self.product.id, 30, 2)
        _, (other_code,) = keys_services.issue_codes(other.id, 30, 1)
        services.activate(codes[0], DEVICE_A, "pc-one")
        services.activate(codes[1], DEVICE_B, "pc-two")
        services.activate(other_code, DEVICE_A)

        self.assertEqual(services.count_by_product(), {"software-a": 2, "software-b": 1})
        self.assertEqual(len(services.search("software-a")), 2)
        found = services.search("software-a", codes[0].lower())
        self.assertEqual([a.code for a in found], [codes[0].replace("-", "")])
        self.assertEqual([a.device_info for a in services.search("software-a", "pc-two")], ["pc-two"])

    def test_in_use_and_delete_by_product(self):
        other = keys_services.create_product("software-b", "B")
        _, codes = keys_services.issue_codes(self.product.id, 30, 3)
        _, (other_code,) = keys_services.issue_codes(other.id, 30, 1)
        services.activate(codes[0], DEVICE_A)
        services.activate(codes[0], DEVICE_B)
        services.activate(codes[1], DEVICE_A)
        services.activate(codes[2], DEVICE_A)
        services.activate(other_code, DEVICE_A)
        Activation.objects.filter(code=codes[1].replace("-", "")).update(disabled=True)
        Activation.objects.filter(code=codes[2].replace("-", "")).update(expires_at=timezone.now())
        self.assertEqual(services.count_in_use_by_product(), {"software-a": 1, "software-b": 1})

        self.assertEqual(services.delete_by_product("software-a"), 3)
        self.assertEqual(services.search("software-a"), [])
        self.assertEqual(TransferLog.objects.count(), 0)
        self.assertEqual(len(services.search("software-b")), 1)

    def test_detail_and_toggle(self):
        services.activate(self.code, DEVICE_A, "pc-a")
        services.activate(self.code, DEVICE_B, "pc-b")
        (info,) = services.search("software-a")
        detail, logs = services.get_detail(info.id)
        self.assertEqual((detail.device_hash, detail.transfer_count, detail.status), (DEVICE_B, 1, "有效"))
        self.assertEqual((logs[0].old_device_info, logs[0].new_device_info), ("pc-a", "pc-b"))

        self.assertEqual(services.toggle_disabled(info.id).status, "已禁用")
        self.assertEqual(services.toggle_disabled(info.id).status, "有效")
        self.assertCode("ACTIVATION_NOT_FOUND", services.get_detail, 999)
        self.assertCode("ACTIVATION_NOT_FOUND", services.toggle_disabled, 999)


class ActivationApiTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", password="pw")
        product = keys_services.create_product("software-a", "A")
        _, (self.code,) = keys_services.issue_codes(product.id, 30, 1)

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_activate_and_verify(self):
        resp = self.post("/api/activate", {"code": self.code, "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])

        resp = self.post("/api/verify", {"device_hash": DEVICE_A, "token": body["token"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["valid"], True)
        (info,) = services.search("software-a")
        self.assertEqual(info.ip, "127.0.0.1")

    def test_request_invalid(self):
        cases = [
            {"code": self.code},
            {"code": self.code, "device_hash": "XYZ"},
            {"code": self.code, "device_hash": DEVICE_A, "device_info": 1},
        ]
        for payload in cases:
            resp = self.post("/api/activate", payload)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")
        resp = self.client.post("/api/activate", data="not json", content_type="application/json")
        self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")

    def test_business_error_format(self):
        resp = self.post("/api/activate", {"code": "NOPE", "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "CODE_INVALID", "message": "卡密格式错误"}})

    def test_no_login_required(self):
        resp = self.post("/api/activate", {"code": self.code, "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)


@override_settings(ALLOWED_HOSTS=["auth.example.com"])
class McpTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", password="pw")

    def rpc(self, method, params=None, request_id=1, **headers):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if request_id is not None:
            message["id"] = request_id
        return self.client.post(
            "/mcp", data=json.dumps(message), content_type="application/json", HTTP_HOST="auth.example.com", **headers
        )

    def test_initialize_and_list_tools(self):
        body = self.rpc("initialize", {"protocolVersion": "2025-06-18"}).json()
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", body["result"]["capabilities"])
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["get_integration_guide"])

    def guide(self, **headers):
        resp = self.rpc("tools/call", {"name": "get_integration_guide"}, **headers)
        return resp.json()["result"]["content"][0]["text"]

    def test_guide_uses_request_host(self):
        text = self.guide()
        self.assertIn("POST http://auth.example.com/api/activate", text)
        self.assertIn("POST http://auth.example.com/api/verify", text)
        self.assertIn("POST http://auth.example.com/api/redeem", text)
        self.assertIn("POST http://auth.example.com/api/redeem/status", text)
        self.assertIn("设备指纹", text)
        self.assertIn("POST https://auth.example.com/api/activate", self.guide(HTTP_X_FORWARDED_PROTO="https"))

    def test_guide_only_client_interface(self):
        text = self.guide()
        self.assertIn("设备激活卡密", text)
        self.assertIn("按次数卡密", text)
        for name in ("POST /api/activate", "POST /api/verify", "POST /api/redeem", "POST /api/redeem/status"):
            self.assertIn(f"name: {name}", text)
        for path in (INTERFACE_PATH, REDEEM_INTERFACE_PATH):
            for error in yaml.safe_load(path.read_text(encoding="utf-8"))["errors"]:
                self.assertIn(error["code"], text)
        for internal in ("activation.services", "redeem.services", "product_code", "delete_by_product", "contract:"):
            self.assertNotIn(internal, text)

    def test_guide_reflects_file_changes_without_restart(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        source_root = settings.BASE_DIR.parent
        sources = (
            "docs/client/activation/README.md",
            "docs/client/fingerprint/README.md",
            "docs/client/redeem/README.md",
            INTERFACE_RELATIVE,
            REDEEM_INTERFACE_RELATIVE,
        )
        for relative in sources:
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source_root / relative, root / relative)
        with override_settings(BASE_DIR=root / "server"):
            self.assertNotIn("NEW_ERROR_CODE", self.guide())
            for relative, code in ((INTERFACE_RELATIVE, "NEW_ERROR_CODE"), (REDEEM_INTERFACE_RELATIVE, "NEW_REDEEM_CODE")):
                spec_path = root / relative
                spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
                spec["errors"].append({"code": code, "desc": "新增错误码"})
                spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
            for relative in ("docs/client/activation/README.md", "docs/client/redeem/README.md"):
                readme = root / relative
                readme.write_text(readme.read_text(encoding="utf-8") + f"\n新增说明 {relative}\n", encoding="utf-8")
            text = self.guide()
        self.assertIn("NEW_ERROR_CODE", text)
        self.assertIn("NEW_REDEEM_CODE", text)
        self.assertIn("新增说明 docs/client/activation/README.md", text)
        self.assertIn("新增说明 docs/client/redeem/README.md", text)

    def test_errors_and_notifications(self):
        self.assertEqual(self.rpc("nope").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"}).json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("notifications/initialized", request_id=None).status_code, 202)
        self.assertEqual(self.client.get("/mcp", HTTP_HOST="auth.example.com").status_code, 405)


class InterfaceErrorsTests(SimpleTestCase):
    def test_raised_codes_are_listed(self):
        listed = {e["code"] for e in yaml.safe_load(INTERFACE_PATH.read_text(encoding="utf-8"))["errors"]}
        module_dir = Path(__file__).resolve().parent
        raised = set()
        for path in module_dir.glob("*.py"):
            if path.name != "tests.py":
                raised |= set(re.findall(r'ServiceError\(\s*"([A-Z_]+)"', path.read_text(encoding="utf-8")))
        self.assertTrue(raised)
        self.assertEqual(raised - listed, set())
