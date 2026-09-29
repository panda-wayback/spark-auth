import csv
import io
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from keys.models import Activation, Product, SigningKey, TransferLog

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


class EndToEndTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-123456")
        self.console = Client()
        self.console.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        self.app = Client()

    def create_product(self, code, allow_transfer=False, penalty_hours=0):
        resp = self.console.post(
            reverse("console:product_create"),
            {"code": code, "name": code, "allow_transfer": "on" if allow_transfer else "", "transfer_penalty_hours": penalty_hours},
        )
        self.assertEqual(resp.status_code, 302)
        return Product.objects.get(code=code)

    def export(self, product, batch=""):
        resp = self.console.get(reverse("console:key_export", args=[product.pk]), {"batch": batch} if batch else {})
        return [row[0] for row in csv.reader(io.StringIO(resp.content.decode("utf-8-sig")))][1:]

    def issue(self, product, count=1, days=30):
        resp = self.console.post(reverse("console:key_issue", args=[product.pk]), {"count": count, "duration_days": days})
        self.assertEqual(resp.status_code, 200)
        return self.export(product, SigningKey.objects.filter(product=product).latest("id").key_id)

    def call(self, endpoint, **payload):
        return self.app.post(f"/api/{endpoint}", data=json.dumps(payload), content_type="application/json")

    def activate(self, product, code, device):
        return self.call("activate", product=product, code=code, device_hash=device)

    def verify(self, product, device, token):
        return self.call("verify", product=product, device_hash=device, token=token)

    def test_activate_and_verify(self):
        code = self.issue(self.create_product("software-a"))[0]
        resp = self.activate("software-a", code, DEVICE_A)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.verify("software-a", DEVICE_A, resp.json()["token"]).status_code, 200)

    def test_batch_export_then_activate(self):
        product = self.create_product("software-a")
        codes = self.issue(product, count=20)
        self.assertEqual(len(set(codes)), 20)
        self.assertEqual(Activation.objects.count(), 0)
        self.assertEqual(self.activate("software-a", codes[5], DEVICE_A).status_code, 200)

    def test_transfer_not_allowed(self):
        code = self.issue(self.create_product("software-a", allow_transfer=False))[0]
        self.activate("software-a", code, DEVICE_A)
        resp = self.activate("software-a", code, DEVICE_B)
        self.assertEqual(resp.json()["error"]["code"], "TRANSFER_NOT_ALLOWED")

    def test_transfer_deducts_one_day(self):
        code = self.issue(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        first = self.activate("software-a", code, DEVICE_A).json()
        second = self.activate("software-a", code, DEVICE_B).json()
        delta = parse_datetime(first["expires_at"]) - parse_datetime(second["expires_at"])
        self.assertEqual(delta, timedelta(days=1))
        self.assertEqual(TransferLog.objects.filter(activation__code=code).count(), 1)
        resp = self.verify("software-a", DEVICE_A, first["token"])
        self.assertEqual(resp.json()["error"]["code"], "DEVICE_MISMATCH")

    def test_transfer_insufficient_time_keeps_old_device(self):
        code = self.issue(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        token = self.activate("software-a", code, DEVICE_A).json()["token"]
        Activation.objects.filter(code=code).update(expires_at=timezone.now() + timedelta(hours=2))
        resp = self.activate("software-a", code, DEVICE_B)
        self.assertEqual(resp.json()["error"]["code"], "TRANSFER_INSUFFICIENT_TIME")
        self.assertEqual(self.verify("software-a", DEVICE_A, token).status_code, 200)

    def test_product_mismatch_and_expired(self):
        code = self.issue(self.create_product("software-a"))[0]
        self.create_product("software-b")
        self.assertEqual(self.activate("software-b", code, DEVICE_A).json()["error"]["code"], "CODE_PRODUCT_MISMATCH")
        self.activate("software-a", code, DEVICE_A)
        Activation.objects.filter(code=code).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.activate("software-a", code, DEVICE_A).json()["error"]["code"], "CODE_EXPIRED")

    def test_disable_batch_keeps_activated_devices(self):
        product = self.create_product("software-a")
        activated, unused = self.issue(product, count=2)
        token = self.activate("software-a", activated, DEVICE_A).json()["token"]
        signing_key = SigningKey.objects.get(product=product)
        self.console.post(reverse("console:signing_key_disable", args=[signing_key.pk]))
        self.assertEqual(self.activate("software-a", unused, DEVICE_B).json()["error"]["code"], "SIGNING_KEY_DISABLED")
        self.assertEqual(self.verify("software-a", DEVICE_A, token).status_code, 200)
