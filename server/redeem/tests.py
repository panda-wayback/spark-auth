import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from keys import services as keys_services
from keys.errors import ServiceError

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
        self.assertEqual((status.uses, status.used, status.remaining), (4, 0, 4))
        services.redeem(code, count=2)
        status = services.status(" " + code.lower() + " ")
        self.assertEqual((status.uses, status.used, status.remaining), (4, 2, 2))

        device = keys_services.create_product("software-b", "B")
        _, (device_code,) = keys_services.issue_codes(device.id, 30, 1)
        self.assertCode("CODE_TYPE_MISMATCH", services.status, device_code)

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

        self.assertEqual(services.delete_by_product("service-a"), 7)
        self.assertEqual(services.search("service-a"), [])
        self.assertEqual(len(services.search("service-b")), 1)

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
        _, (record,) = services.get_code_detail(self.codes[0])
        self.assertEqual(record.ip, "127.0.0.1")

        resp = self.post("/api/redeem", {"code": self.codes[0]})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": {"code": "CODE_USED", "message": "卡密次数已用完"}})

    def test_redeem_count_api(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=5)
        resp = self.post("/api/redeem", {"code": code, "count": 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["remaining"], 3)

        resp = self.post("/api/redeem", {"code": code, "count": 4})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "CODE_USED")
        self.assertEqual(resp.json()["error"]["message"], "扣减超过剩余次数，卡密已作废")

        resp = self.post("/api/redeem/status", {"code": code})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "uses": 5, "used": 6, "remaining": -1})

    def test_status_api(self):
        _, (code,) = keys_services.issue_codes(self.product.id, None, 1, uses=3)
        resp = self.post("/api/redeem/status", {"code": code})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "uses": 3, "used": 0, "remaining": 3})

    def test_request_invalid(self):
        for payload in ({}, {"code": ""}, {"code": 1}, ["x"], {"code": self.codes[0], "count": 0}):
            resp = self.post("/api/redeem", payload)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"]["code"], "REQUEST_INVALID")
        resp = self.client.post("/api/redeem", data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        resp = self.post("/api/redeem/status", {})
        self.assertEqual(resp.status_code, 400)
