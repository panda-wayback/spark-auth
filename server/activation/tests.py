import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from keys import services as keys_services
from keys.errors import ServiceError
from keys.models import Activation, Product, SigningKey, TransferLog

from . import services

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


class ActivationServiceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            code="software-a", name="A", allow_transfer=True, transfer_penalty_hours=24
        )
        self.signing_key, (self.code,) = keys_services.issue_codes(self.product, 30, 1)

    def assertCode(self, code, func, *args):
        with self.assertRaises(ServiceError) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.code, code)

    def activation(self):
        return Activation.objects.get(code=self.code)

    def test_first_activation_sets_expiry_without_log(self):
        before = timezone.now()
        _, expires_at = services.activate("software-a", self.code, DEVICE_A, "pc-a")
        self.assertGreaterEqual(expires_at, before + timedelta(days=30))
        self.assertLessEqual(expires_at, timezone.now() + timedelta(days=30))
        self.assertEqual(self.activation().device_hash, DEVICE_A)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_unactivated_code_does_not_expire(self):
        SigningKey.objects.filter(pk=self.signing_key.pk).update(created_at=timezone.now() - timedelta(days=400))
        _, expires_at = services.activate("software-a", self.code, DEVICE_A)
        self.assertGreater(expires_at, timezone.now() + timedelta(days=29))

    def test_same_device_keeps_expiry(self):
        _, first = services.activate("software-a", self.code, DEVICE_A)
        _, second = services.activate("software-a", self.code, DEVICE_A)
        self.assertEqual(first, second)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_transfer_not_allowed(self):
        Product.objects.filter(pk=self.product.pk).update(allow_transfer=False)
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
        Product.objects.filter(pk=self.product.pk).update(transfer_penalty_hours=0)
        _, before = services.activate("software-a", self.code, DEVICE_A)
        _, after = services.activate("software-a", self.code, DEVICE_B)
        self.assertEqual(before, after)
        self.assertEqual(TransferLog.objects.count(), 1)

    def test_transfer_insufficient_time_changes_nothing(self):
        services.activate("software-a", self.code, DEVICE_A)
        almost = timezone.now() + timedelta(hours=10)
        Activation.objects.filter(code=self.code).update(expires_at=almost)
        self.assertCode("TRANSFER_INSUFFICIENT_TIME", services.activate, "software-a", self.code, DEVICE_B)
        activation = self.activation()
        self.assertEqual((activation.expires_at, activation.device_hash), (almost, DEVICE_A))
        self.assertEqual(TransferLog.objects.count(), 0)

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
        other = Product.objects.create(code="software-b", name="B")
        _, (other_code,) = keys_services.issue_codes(other, 30, 1)
        tampered = self.code[:20] + ("A" if self.code[20] != "A" else "B") + self.code[21:]
        self.assertCode("PRODUCT_NOT_FOUND", services.activate, "nope", self.code, DEVICE_A)
        self.assertCode("CODE_INVALID", services.activate, "software-a", "NOPE", DEVICE_A)
        self.assertCode("CODE_INVALID", services.activate, "software-a", tampered, DEVICE_A)
        self.assertCode("CODE_PRODUCT_MISMATCH", services.activate, "software-a", other_code, DEVICE_A)
        self.assertCode("CODE_PRODUCT_MISMATCH", services.activate, "software-b", self.code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_unknown_batch(self):
        SigningKey.objects.filter(pk=self.signing_key.pk).update(key_id="0" * 16)
        self.assertCode("SIGNING_KEY_NOT_FOUND", services.activate, "software-a", self.code, DEVICE_A)

    def test_disabled_and_expired(self):
        token, _ = services.activate("software-a", self.code, DEVICE_A)

        Activation.objects.filter(code=self.code).update(disabled=True)
        self.assertCode("CODE_DISABLED", services.verify, "software-a", DEVICE_A, token)
        self.assertCode("CODE_DISABLED", services.activate, "software-a", self.code, DEVICE_A)
        Activation.objects.filter(code=self.code).update(disabled=False)

        Product.objects.filter(pk=self.product.pk).update(disabled=True)
        self.assertCode("PRODUCT_DISABLED", services.verify, "software-a", DEVICE_A, token)
        Product.objects.filter(pk=self.product.pk).update(disabled=False)

        Activation.objects.filter(code=self.code).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertCode("CODE_EXPIRED", services.verify, "software-a", DEVICE_A, token)
        self.assertCode("CODE_EXPIRED", services.activate, "software-a", self.code, DEVICE_A)

    def test_signing_key_disabled_blocks_new_activation(self):
        SigningKey.objects.filter(pk=self.signing_key.pk).update(disabled=True)
        self.assertCode("SIGNING_KEY_DISABLED", services.activate, "software-a", self.code, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 0)

    def test_signing_key_disabled_keeps_activated(self):
        services.activate("software-a", self.code, DEVICE_A)
        SigningKey.objects.filter(pk=self.signing_key.pk).update(disabled=True)
        token, expires_at = services.activate("software-a", self.code, DEVICE_B)
        self.assertEqual(services.verify("software-a", DEVICE_B, token), expires_at)

    def test_formatted_input_matches_same_activation(self):
        services.activate("software-a", self.code, DEVICE_A)
        messy = " " + "-".join(self.code[i : i + 5] for i in range(0, len(self.code), 5)).lower()
        services.activate("software-a", messy, DEVICE_A)
        self.assertEqual(Activation.objects.count(), 1)

    def test_policy_change_applies_immediately(self):
        services.activate("software-a", self.code, DEVICE_A)
        Product.objects.filter(pk=self.product.pk).update(allow_transfer=False)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, "software-a", self.code, DEVICE_B)


class ActivationApiTests(TestCase):
    def setUp(self):
        product = Product.objects.create(code="software-a", name="A")
        _, (self.code,) = keys_services.issue_codes(product, 30, 1)

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
