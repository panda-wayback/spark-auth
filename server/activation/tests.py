import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from keys.errors import ServiceError
from keys.models import Activation, LicenseKey, Product, TransferLog

from . import services

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


class ActivationServiceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            code="software-a", name="A", allow_transfer=True, transfer_penalty_hours=24
        )
        self.lic = LicenseKey.objects.create(key="KEY-1", product=self.product, duration_days=30)

    def assertCode(self, code, func, *args):
        with self.assertRaises(ServiceError) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_first_activation_sets_expiry_without_log(self):
        before = timezone.now()
        _, expires_at = services.activate("software-a", "KEY-1", DEVICE_A, "pc-a")
        self.assertGreaterEqual(expires_at, before + timedelta(days=30))
        self.assertLessEqual(expires_at, timezone.now() + timedelta(days=30))
        self.assertEqual(TransferLog.objects.count(), 0)
        self.assertEqual(Activation.objects.get(key=self.lic).device_hash, DEVICE_A)

    def test_same_device_keeps_expiry(self):
        _, first = services.activate("software-a", "KEY-1", DEVICE_A)
        _, second = services.activate("software-a", "KEY-1", DEVICE_A)
        self.assertEqual(first, second)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_transfer_not_allowed(self):
        self.product.allow_transfer = False
        self.product.save()
        services.activate("software-a", "KEY-1", DEVICE_A)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, "software-a", "KEY-1", DEVICE_B)
        self.assertEqual(Activation.objects.get(key=self.lic).device_hash, DEVICE_A)

    def test_transfer_deducts_and_logs(self):
        _, before = services.activate("software-a", "KEY-1", DEVICE_A, "pc-a")
        _, after = services.activate("software-a", "KEY-1", DEVICE_B, "pc-b")
        self.assertEqual(before - after, timedelta(hours=24))
        log = TransferLog.objects.get(key=self.lic)
        self.assertEqual(
            (log.old_device_hash, log.old_device_info, log.new_device_hash, log.new_device_info),
            (DEVICE_A, "pc-a", DEVICE_B, "pc-b"),
        )
        self.assertEqual((log.penalty_hours, log.expires_before, log.expires_after), (24, before, after))

    def test_transfer_with_zero_penalty(self):
        self.product.transfer_penalty_hours = 0
        self.product.save()
        _, before = services.activate("software-a", "KEY-1", DEVICE_A)
        _, after = services.activate("software-a", "KEY-1", DEVICE_B)
        self.assertEqual(before, after)
        self.assertEqual(TransferLog.objects.count(), 1)

    def test_transfer_insufficient_time_changes_nothing(self):
        services.activate("software-a", "KEY-1", DEVICE_A)
        almost = timezone.now() + timedelta(hours=10)
        LicenseKey.objects.filter(pk=self.lic.pk).update(expires_at=almost)
        self.assertCode("TRANSFER_INSUFFICIENT_TIME", services.activate, "software-a", "KEY-1", DEVICE_B)
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.expires_at, almost)
        self.assertEqual(Activation.objects.get(key=self.lic).device_hash, DEVICE_A)
        self.assertEqual(TransferLog.objects.count(), 0)

    def test_old_device_fails_after_transfer(self):
        token_a, _ = services.activate("software-a", "KEY-1", DEVICE_A)
        services.activate("software-a", "KEY-1", DEVICE_B)
        self.assertCode("DEVICE_MISMATCH", services.verify, "software-a", DEVICE_A, token_a)

    def test_verify_success(self):
        token, expires_at = services.activate("software-a", "KEY-1", DEVICE_A)
        self.assertEqual(services.verify("software-a", DEVICE_A, token), expires_at)

    def test_verify_token_used_on_other_device(self):
        token, _ = services.activate("software-a", "KEY-1", DEVICE_A)
        self.assertCode("DEVICE_MISMATCH", services.verify, "software-a", DEVICE_B, token)

    def test_tampered_token(self):
        token, _ = services.activate("software-a", "KEY-1", DEVICE_A)
        self.assertCode("TOKEN_INVALID", services.verify, "software-a", DEVICE_A, token[:-2] + "xx")
        self.assertCode("TOKEN_INVALID", services.verify, "software-a", DEVICE_A, "garbage")

    def test_key_and_product_checks(self):
        Product.objects.create(code="software-b", name="B")
        self.assertCode("PRODUCT_NOT_FOUND", services.activate, "nope", "KEY-1", DEVICE_A)
        self.assertCode("KEY_NOT_FOUND", services.activate, "software-a", "NOPE", DEVICE_A)
        self.assertCode("KEY_PRODUCT_MISMATCH", services.activate, "software-b", "KEY-1", DEVICE_A)

    def test_disabled_and_expired(self):
        token, _ = services.activate("software-a", "KEY-1", DEVICE_A)

        LicenseKey.objects.filter(pk=self.lic.pk).update(disabled=True)
        self.assertCode("KEY_DISABLED", services.verify, "software-a", DEVICE_A, token)
        LicenseKey.objects.filter(pk=self.lic.pk).update(disabled=False)

        Product.objects.filter(pk=self.product.pk).update(disabled=True)
        self.assertCode("PRODUCT_DISABLED", services.verify, "software-a", DEVICE_A, token)
        Product.objects.filter(pk=self.product.pk).update(disabled=False)

        LicenseKey.objects.filter(pk=self.lic.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertCode("KEY_EXPIRED", services.verify, "software-a", DEVICE_A, token)
        self.assertCode("KEY_EXPIRED", services.activate, "software-a", "KEY-1", DEVICE_A)

    def test_policy_change_applies_immediately(self):
        services.activate("software-a", "KEY-1", DEVICE_A)
        Product.objects.filter(pk=self.product.pk).update(allow_transfer=False)
        self.assertCode("TRANSFER_NOT_ALLOWED", services.activate, "software-a", "KEY-1", DEVICE_B)


class ActivationApiTests(TestCase):
    def setUp(self):
        product = Product.objects.create(code="software-a", name="A")
        LicenseKey.objects.create(key="KEY-1", product=product, duration_days=30)

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_activate_and_verify(self):
        resp = self.post("/api/activate", {"product": "software-a", "key": "KEY-1", "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])

        resp = self.post("/api/verify", {"product": "software-a", "device_hash": DEVICE_A, "token": body["token"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["valid"], True)

    def test_request_invalid(self):
        cases = [
            {"product": "software-a", "key": "KEY-1"},
            {"product": "software-a", "key": "KEY-1", "device_hash": "XYZ"},
            {"product": "software-a", "key": "KEY-1", "device_hash": DEVICE_A, "device_info": 1},
        ]
        for payload in cases:
            resp = self.post("/api/activate", payload)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")
        resp = self.client.post("/api/activate", data="not json", content_type="application/json")
        self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")

    def test_business_error_format(self):
        resp = self.post("/api/activate", {"product": "software-a", "key": "NOPE", "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "KEY_NOT_FOUND", "message": "key 不存在"}})

    def test_no_login_required(self):
        resp = self.post("/api/activate", {"product": "software-a", "key": "KEY-1", "device_hash": DEVICE_A})
        self.assertEqual(resp.status_code, 200)
