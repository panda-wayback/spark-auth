import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from keys import codes as key_codes
from keys import services as keys_services
from keys.errors import ServiceError
from keys.models import Batch

from . import services
from .models import Redemption


class RedeemTestCase(TestCase):
    def setUp(self):
        self.product = keys_services.create_product("service-a", "A", kind="count")
        self.batch, self.codes = keys_services.issue_codes(self.product.id, None, 3, uses=1)

    def assertCode(self, code, func, *args, **kwargs):
        with self.assertRaises(ServiceError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)


class RedeemServiceTests(RedeemTestCase):
    def test_single_use(self):
        info = services.redeem(self.codes[0])
        self.assertEqual(
            (info.code, info.product_code, info.seq, info.remaining), (self.codes[0].replace("-", ""), "service-a", 1, 0)
        )
        self.assertIsNotNone(info.expires_at)
        self.assertEqual(Redemption.objects.count(), 1)

        self.assertCode("CODE_USED", services.redeem, self.codes[0])
        self.assertCode("CODE_USED", services.redeem, " " + self.codes[0].lower().replace("-", " ") + " ")
        self.assertEqual(Redemption.objects.count(), 1)

    def test_multiple_uses(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=3)
        self.assertEqual([services.redeem(code).remaining for _ in range(3)], [2, 1, 0])
        self.assertCode("CODE_USED", services.redeem, code)
        usage, records = services.get_code_detail(code.lower())
        self.assertEqual((usage.used, usage.uses, usage.remaining, usage.status), (3, 3, 0, "已用完"))
        self.assertEqual([r.seq for r in records], [3, 2, 1])
        self.assertEqual((usage.first_redeemed_at, usage.last_redeemed_at), (records[-1].redeemed_at, records[0].redeemed_at))
        self.assertCode("REDEMPTION_NOT_FOUND", services.get_code_detail, self.codes[0])

    def test_redeem_count_and_overuse(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=5)
        self.assertEqual(services.redeem(code, count=3).remaining, 2)
        self.assertEqual(Redemption.objects.filter(code=code.replace("-", "")).count(), 3)

        self.assertCode("CODE_USED", services.redeem, code, count=3)
        usage, records = services.get_code_detail(code)
        self.assertEqual((usage.used, usage.uses, usage.remaining, usage.status), (6, 5, -1, "已用完"))
        self.assertEqual([r.seq for r in records], [6, 5, 4, 3, 2, 1])
        status = services.status(code)
        self.assertEqual((status.uses, status.used, status.remaining), (5, 6, -1))
        self.assertCode("CODE_USED", services.redeem, code)

    def test_status(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=4)
        status = services.status(code)
        self.assertEqual((status.uses, status.used, status.remaining, status.expires_at, status.expired), (4, 0, 4, None, False))
        result = services.redeem(code, count=2)
        status = services.status(" " + code.lower() + " ")
        self.assertEqual((status.uses, status.used, status.remaining), (4, 2, 2))
        self.assertEqual(status.expires_at, result.expires_at)
        self.assertFalse(status.expired)

        device = keys_services.create_product("software-b", "B")
        _, (device_code,) = keys_services.issue_codes(device.id, 30, 1)
        self.assertCode("CODE_TYPE_MISMATCH", services.status, device_code)

    def test_expiry_from_first_redeem(self):
        _, (code,) = keys_services.issue_codes(self.product.id, 1, 1, uses=5)
        before = timezone.now()
        result = services.redeem(code)
        self.assertGreaterEqual(result.expires_at, before + timedelta(days=1))
        self.assertLessEqual(result.expires_at, timezone.now() + timedelta(days=1))
        self.assertEqual(result.remaining, 4)

        Redemption.objects.filter(code=code.replace("-", "")).update(
            redeemed_at=timezone.now() - timedelta(days=2)
        )
        self.assertCode("CODE_EXPIRED", services.redeem, code)
        self.assertEqual(Redemption.objects.filter(code=code.replace("-", "")).count(), 1)
        status = services.status(code)
        self.assertTrue(status.expired)
        self.assertEqual(status.remaining, 4)
        usage, _ = services.get_code_detail(code)
        self.assertEqual(usage.status, "已到期")

    def test_legacy_batch_without_duration_never_expires(self):
        batch = Batch.objects.create(
            batch_id=key_codes.new_batch_id(),
            secret=key_codes.new_batch_secret(),
            product_id=self.product.id,
            duration_days=None,
            uses=2,
            count=1,
            created_at=timezone.now().replace(microsecond=0),
        )
        code = key_codes.sign(batch.batch_id, batch.secret, 1)
        result = services.redeem(code)
        self.assertIsNone(result.expires_at)
        Redemption.objects.filter(code=code.replace("-", "")).update(
            redeemed_at=timezone.now() - timedelta(days=400)
        )
        self.assertEqual(services.redeem(code).remaining, 0)
        self.assertFalse(services.status(code).expired)

    def test_count_invalid(self):
        for count in (0, -1, 1001, True, 1.5, "1"):
            self.assertCode("REQUEST_INVALID", services.redeem, self.codes[0], count=count)
        self.assertEqual(Redemption.objects.count(), 0)
        self.assertEqual(services.redeem(self.codes[0], count=1).remaining, 0)

    def test_rejections_write_nothing(self):
        plain = self.codes[0].replace("-", "")
        tampered = plain[:20] + ("A" if plain[20] != "A" else "B") + plain[21:]
        self.assertCode("CODE_INVALID", services.redeem, tampered)
        self.assertCode("CODE_INVALID", services.redeem, "NOPE")

        keys_services.update_product(self.product.id, "A", False, 0, True)
        self.assertCode("PRODUCT_DISABLED", services.redeem, self.codes[1])
        keys_services.update_product(self.product.id, "A", False, 0, False)

        keys_services.disable_batch(self.batch.batch_id)
        self.assertCode("BATCH_DISABLED", services.redeem, self.codes[2])
        self.assertEqual(Redemption.objects.count(), 0)

    def test_device_code_rejected(self):
        device = keys_services.create_product("software-b", "B")
        _, (code,) = keys_services.issue_codes(device.id, 30, 1)
        self.assertCode("CODE_TYPE_MISMATCH", services.redeem, code)
        self.assertEqual(Redemption.objects.count(), 0)

    def test_append_only(self):
        services.redeem(self.codes[0])
        redemption = Redemption.objects.get()
        with self.assertRaises(PermissionError):
            redemption.save()
        with self.assertRaises(PermissionError):
            redemption.delete()

    def test_in_use_and_delete_by_product(self):
        _, (partial, used_up, over) = keys_services.issue_codes(self.product.id, None, 3, uses=2)
        other = keys_services.create_product("service-b", "B", kind="count")
        _, (other_code,) = keys_services.issue_codes(other.id, None, 1, uses=1)
        services.redeem(partial)
        services.redeem(used_up)
        services.redeem(used_up)
        self.assertCode("CODE_USED", services.redeem, over, count=3)
        services.redeem(self.codes[0])
        services.redeem(other_code)
        self.assertEqual(services.count_in_use_by_product(), {"service-a": 1})

        _, (expired_code,) = keys_services.issue_codes(self.product.id, 1, 1, uses=3)
        services.redeem(expired_code)
        Redemption.objects.filter(code=expired_code.replace("-", "")).update(
            redeemed_at=timezone.now() - timedelta(days=2)
        )
        self.assertEqual(services.count_in_use_by_product(), {"service-a": 1})

        services.toggle_disabled(partial)
        self.assertEqual(services.count_in_use_by_product(), {})

        self.assertEqual(services.delete_by_product("service-a"), 8)
        self.assertEqual(services.search("service-a"), [])
        self.assertEqual(len(services.search("service-b")), 1)
        self.assertCode("REDEMPTION_NOT_FOUND", services.toggle_disabled, partial)

    def test_toggle_disabled(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=3)
        services.redeem(code)
        usage = services.toggle_disabled(code)
        self.assertTrue(usage.disabled)
        self.assertEqual(usage.status, "已禁用")
        self.assertCode("CODE_DISABLED", services.redeem, code)
        self.assertCode("CODE_DISABLED", services.status, code)
        usage = services.toggle_disabled(" " + code.lower() + " ")
        self.assertFalse(usage.disabled)
        self.assertEqual(usage.status, "使用中")
        self.assertEqual(services.redeem(code).remaining, 1)
        self.assertCode("REDEMPTION_NOT_FOUND", services.toggle_disabled, self.codes[1])

    def test_count_and_search(self):
        other = keys_services.create_product("service-b", "B", kind="count")
        _, (other_code,) = keys_services.issue_codes(other.id, None, 1, uses=1)
        _, (multi,) = keys_services.issue_codes(self.product.id, None, 1, uses=5)
        services.redeem(self.codes[0])
        services.redeem(multi)
        services.redeem(multi)
        services.redeem(other_code)

        self.assertEqual(services.count_by_product(), {"service-a": 3, "service-b": 1})
        usages = services.search("service-a")
        self.assertEqual(
            [(u.code, u.used, u.uses, u.status) for u in usages],
            [(multi.replace("-", ""), 2, 5, "使用中"), (self.codes[0].replace("-", ""), 1, 1, "已用完")],
        )
        self.assertIsNotNone(usages[0].expires_at)
        found = services.search("service-a", multi.lower())
        self.assertEqual([u.code for u in found], [multi.replace("-", "")])


class RedeemApiTests(RedeemTestCase):
    def setUp(self):
        super().setUp()
        get_user_model().objects.create_superuser("admin", password="pw")

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def test_redeem_without_login(self):
        resp = self.post("/api/redeem", {"code": self.codes[0]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["remaining"], 0)
        self.assertIn("redeemed_at", body)
        self.assertIsNotNone(body["expires_at"])
        _, (record,) = services.get_code_detail(self.codes[0])
        self.assertEqual(record.ip, "127.0.0.1")

        resp = self.post("/api/redeem", {"code": self.codes[0]})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "CODE_USED", "message": "卡密次数已用完"}})

    def test_redeem_count_api(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=5)
        resp = self.post("/api/redeem", {"code": code, "count": 2})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["remaining"], 3)
        self.assertIsNotNone(body["expires_at"])

        resp = self.post("/api/redeem", {"code": code, "count": 4})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "CODE_USED")
        self.assertEqual(resp.json()["error"]["message"], "扣减超过剩余次数，卡密已作废")

        resp = self.post("/api/redeem/status", {"code": code})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual((body["uses"], body["used"], body["remaining"]), (5, 6, -1))
        self.assertIsNotNone(body["expires_at"])
        self.assertFalse(body["expired"])

    def test_status_api(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=3)
        resp = self.post("/api/redeem/status", {"code": code})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "uses": 3, "used": 0, "remaining": 3, "expires_at": None, "expired": False},
        )

    def test_expired_api(self):
        _, (code,) = keys_services.issue_codes(self.product.id, 1, 1, uses=3)
        self.post("/api/redeem", {"code": code})
        Redemption.objects.filter(code=code.replace("-", "")).update(
            redeemed_at=timezone.now() - timedelta(days=2)
        )
        resp = self.post("/api/redeem", {"code": code})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "CODE_EXPIRED")
        resp = self.post("/api/redeem/status", {"code": code})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["expired"])
        self.assertEqual(body["remaining"], 2)

    def test_request_invalid(self):
        for payload in ({}, {"code": ""}, {"code": 1}, ["x"], {"code": self.codes[0], "count": 0}):
            resp = self.post("/api/redeem", payload)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")
        resp = self.client.post("/api/redeem", data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        resp = self.post("/api/redeem/status", {})
        self.assertEqual(resp.status_code, 400)
