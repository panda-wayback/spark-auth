import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from keys import services as keys_services
from keys.errors import ServiceError

from . import services
from .models import Activation, TransferLog

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


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
        _, expires_at = services.activate("software-a", self.code, DEVICE_A, "pc-a")
        self.assertGreaterEqual(expires_at, before + timedelta(days=30))
        self.assertLessEqual(expires_at, timezone.now() + timedelta(days=30))
        self.assertEqual(self.activation().device_hash, DEVICE_A)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_unactivated_code_does_not_expire(self):
        later = timezone.now() + timedelta(days=400)
        with patch("django.utils.timezone.now", return_value=later):
            _, expires_at = services.activate("software-a", self.code, DEVICE_A)
        self.assertEqual(expires_at, later + timedelta(days=30))

    def test_same_device_keeps_expiry(self):
        _, first = services.activate("software-a", self.code, DEVICE_A)
        _, second = services.activate("software-a", self.code, DEVICE_A)
        self.assertEqual(first, second)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_transfer_not_allowed(self):
        set_policy(self.product, allow_transfer=False)
        services.activate("software-a", self.code, DEVICE_A)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, "software-a", self.code, DEVICE_B)
        self.assertEqual(self.activation().device_hash, DEVICE_A)

    def test_transfer_deducts_and_logs(self):
        _, before = services.activate("software-a", self.code, DEVICE_A, "pc-a")
        _, after = services.activate("software-a", self.code, DEVICE_B, "pc-b")
        self.assertEqual(before - after, timedelta(hours=24))
        log = self.activation().transfer_logs.get()
        self.assertEqual(
            (log.old_device_hash, log.old_device_info, log.new_device_hash, log.new_device_info),
            (DEVICE_A, "pc-a", DEVICE_B, "pc-b"),
        )
        self.assertEqual((log.penalty_hours, log.expires_before, log.expires_after), (24, before, after))

    def test_transfer_with_zero_penalty(self):
        set_policy(self.product, penalty=0)
        _, before = services.activate("software-a", self.code, DEVICE_A)
        _, after = services.activate("software-a", self.code, DEVICE_B)
        self.assertEqual(before, after)
        self.assertEqual(TransferLog.objects.count(), 1)

    def test_transfer_insufficient_time_changes_nothing(self):
        services.activate("software-a", self.code, DEVICE_A)
        almost = timezone.now() + timedelta(hours=10)
        Activation.objects.update(expires_at=almost)
        self.assertCode("TRANSFER_INSUFFICIENT_TIME", services.activate, "software-a", self.code, DEVICE_B)
        activation = self.activation()
        self.assertEqual((activation.expires_at, activation.device_hash), (almost, DEVICE_A))
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_transfer_log_append_only(self):
        services.activate("software-a", self.code, DEVICE_A)
        services.activate("software-a", self.code, DEVICE_B)
        log = TransferLog.objects.get()
        with self.assertRaises(PermissionError):
            log.save()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_old_device_fails_after_transfer(self):
        token_a, _ = services.activate("software-a", self.code, DEVICE_A)
        services.activate("software-a", self.code, DEVICE_B)
        self.assertCode("DEVICE_MISMATCH", services.verify, "software-a", DEVICE_A, token_a)

    def test_verify_success(self):
        token, expires_at = services.activate("software-a", self.code, DEVICE_A)
        self.assertEqual(services.verify("software-a", DEVICE_A, token), expires_at)

    def test_verify_token_used_on_other_device(self):
        token, _ = services.activate("software-a", self.code, DEVICE_A)
        self.assertCode("DEVICE_MISMATCH", services.verify, "software-a", DEVICE_B, token)

    def test_tampered_token(self):
        token, _ = services.activate("software-a", self.code, DEVICE_A)
        self.assertCode("TOKEN_INVALID", services.verify, "software-a", DEVICE_A, token[:-2] + "xx")
        self.assertCode("TOKEN_INVALID", services.verify, "software-a", DEVICE_A, "garbage")

    def test_product_and_code_checks(self):
        other = keys_services.create_product("software-b", "B")
        _, (other_code,) = keys_services.issue_codes(other.id, 30, 1)
        plain = self.code.replace("-", "")
        tampered = plain[:20] + ("A" if plain[20] != "A" else "B") + plain[21:]
        self.assertCode("PRODUCT_NOT_FOUND", services.activate, "nope", self.code, DEVICE_A)
        self.assertCode("CODE_INVALID", services.activate, "software-a", "NOPE", DEVICE_A)
        self.assertCode("CODE_INVALID", services.activate, "software-a", tampered, DEVICE_A)
        self.assertCode("CODE_PRODUCT_MISMATCH", services.activate, "software-a", other_code, DEVICE_A)
        self.assertCode("CODE_PRODUCT_MISMATCH", services.activate, "software-b", self.code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_disabled_and_expired(self):
        token, _ = services.activate("software-a", self.code, DEVICE_A)

        Activation.objects.update(disabled=True)
        self.assertCode("CODE_DISABLED", services.verify, "software-a", DEVICE_A, token)
        self.assertCode("CODE_DISABLED", services.activate, "software-a", self.code, DEVICE_A)
        Activation.objects.update(disabled=False)

        set_policy(self.product, disabled=True)
        self.assertCode("PRODUCT_DISABLED", services.verify, "software-a", DEVICE_A, token)
        set_policy(self.product, disabled=False)

        Activation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertCode("CODE_EXPIRED", services.verify, "software-a", DEVICE_A, token)
        self.assertCode("CODE_EXPIRED", services.activate, "software-a", self.code, DEVICE_A)

    def test_batch_disabled_blocks_new_activation(self):
        keys_services.disable_batch(self.batch.batch_id)
        self.assertCode("BATCH_DISABLED", services.activate, "software-a", self.code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_batch_disabled_keeps_activated(self):
        services.activate("software-a", self.code, DEVICE_A)
        keys_services.disable_batch(self.batch.batch_id)
        token, expires_at = services.activate("software-a", self.code, DEVICE_B)
        self.assertEqual(services.verify("software-a", DEVICE_B, token), expires_at)

    def test_formatted_input_matches_same_activation(self):
        services.activate("software-a", self.code, DEVICE_A)
        messy = " " + self.code.replace("-", "").lower()
        services.activate("software-a", messy, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 1)

    def test_policy_change_applies_immediately(self):
        services.activate("software-a", self.code, DEVICE_A)
        set_policy(self.product, allow_transfer=False)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, "software-a", self.code, DEVICE_B)


class AdminQueryTests(ActivationTestCase):
    def test_count_and_search(self):
        other = keys_services.create_product("software-b", "B")
        _, codes = keys_services.issue_codes(self.product.id, 30, 2)
        _, (other_code,) = keys_services.issue_codes(other.id, 30, 1)
        services.activate("software-a", codes[0], DEVICE_A, "pc-one")
        services.activate("software-a", codes[1], DEVICE_B, "pc-two")
        services.activate("software-b", other_code, DEVICE_A)

        self.assertEqual(services.count_by_product(), {"software-a": 2, "software-b": 1})
        self.assertEqual(len(services.search("software-a")), 2)
        found = services.search("software-a", codes[0].lower())
        self.assertEqual([a.code for a in found], [codes[0].replace("-", "")])
        self.assertEqual([a.device_info for a in services.search("software-a", "pc-two")], ["pc-two"])

    def test_detail_and_toggle(self):
        services.activate("software-a", self.code, DEVICE_A, "pc-a")
        services.activate("software-a", self.code, DEVICE_B, "pc-b")
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
        product = keys_services.create_product("software-a", "A")
        _, (self.code,) = keys_services.issue_codes(product.id, 30, 1)

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_activate_and_verify(self):
        resp = self.post("/api/activate", {"product": "software-a", "code": self.code, "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])

        resp = self.post("/api/verify", {"product": "software-a", "device_hash": DEVICE_A, "token": body["token"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["valid"], True)

    def test_request_invalid(self):
        cases = [
            {"product": "software-a", "code": self.code},
            {"product": "software-a", "code": self.code, "device_hash": "XYZ"},
            {"product": "software-a", "code": self.code, "device_hash": DEVICE_A, "device_info": 1},
        ]
        for payload in cases:
            resp = self.post("/api/activate", payload)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")
        resp = self.client.post("/api/activate", data="not json", content_type="application/json")
        self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")

    def test_business_error_format(self):
        resp = self.post("/api/activate", {"product": "software-a", "code": "NOPE", "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "CODE_INVALID", "message": "卡密格式错误"}})
        resp = self.post("/api/activate", {"product": "nope", "code": self.code, "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 404)

    def test_no_login_required(self):
        resp = self.post("/api/activate", {"product": "software-a", "code": self.code, "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)


@override_settings(ALLOWED_HOSTS=["auth.example.com"])
class McpTests(TestCase):
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
        self.assertEqual([t["name"] for t in tools], ["get_activation_guide"])

    def test_guide_uses_request_host(self):
        text = self.rpc("tools/call", {"name": "get_activation_guide"}).json()["result"]["content"][0]["text"]
        self.assertIn("POST http://auth.example.com/api/activate", text)
        self.assertIn("POST http://auth.example.com/api/verify", text)
        self.assertIn("TOKEN_INVALID", text)
        self.assertIn("设备指纹", text)
        resp = self.rpc("tools/call", {"name": "get_activation_guide"}, HTTP_X_FORWARDED_PROTO="https")
        self.assertIn("POST https://auth.example.com/api/activate", resp.json()["result"]["content"][0]["text"])

    def test_errors_and_notifications(self):
        self.assertEqual(self.rpc("nope").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"}).json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("notifications/initialized", request_id=None).status_code, 202)
        self.assertEqual(self.client.get("/mcp", HTTP_HOST="auth.example.com").status_code, 405)
