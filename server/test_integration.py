import csv
import io
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from keys.models import LicenseKey, Product, TransferLog

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

    def generate(self, product, count=1, days=30):
        self.console.post(reverse("console:key_generate", args=[product.pk]), {"count": count, "duration_days": days})
        return list(LicenseKey.objects.filter(product=product).values_list("key", flat=True))

    def export(self, product):
        return self.console.get(reverse("console:key_export", args=[product.pk])).content

    def call(self, endpoint, **payload):
        return self.app.post(f"/api/{endpoint}", data=json.dumps(payload), content_type="application/json")

    def activate(self, product, key, device):
        return self.call("activate", product=product, key=key, device_hash=device)

    def verify(self, product, device, token):
        return self.call("verify", product=product, device_hash=device, token=token)

    def test_activate_and_verify(self):
        key = self.generate(self.create_product("software-a"))[0]
        resp = self.activate("software-a", key, DEVICE_A)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.verify("software-a", DEVICE_A, resp.json()["token"]).status_code, 200)

    def test_batch_export_then_activate(self):
        product = self.create_product("software-a")
        self.generate(product, count=20)
        rows = list(csv.reader(io.StringIO(self.export(product).decode("utf-8-sig"))))
        self.assertEqual(len(rows), 21)
        self.assertEqual(self.activate("software-a", rows[5][0], DEVICE_A).status_code, 200)

    def test_transfer_not_allowed(self):
        key = self.generate(self.create_product("software-a", allow_transfer=False))[0]
        self.activate("software-a", key, DEVICE_A)
        resp = self.activate("software-a", key, DEVICE_B)
        self.assertEqual(resp.json()["error"]["code"], "TRANSFER_NOT_ALLOWED")

    def test_transfer_deducts_one_day(self):
        key = self.generate(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        first = self.activate("software-a", key, DEVICE_A).json()
        second = self.activate("software-a", key, DEVICE_B).json()
        delta = parse_datetime(first["expires_at"]) - parse_datetime(second["expires_at"])
        self.assertEqual(delta, timedelta(days=1))
        self.assertEqual(TransferLog.objects.filter(key__key=key).count(), 1)
        resp = self.verify("software-a", DEVICE_A, first["token"])
        self.assertEqual(resp.json()["error"]["code"], "DEVICE_MISMATCH")

    def test_transfer_insufficient_time_keeps_old_device(self):
        key = self.generate(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        token = self.activate("software-a", key, DEVICE_A).json()["token"]
        LicenseKey.objects.filter(key=key).update(expires_at=timezone.now() + timedelta(hours=2))
        resp = self.activate("software-a", key, DEVICE_B)
        self.assertEqual(resp.json()["error"]["code"], "TRANSFER_INSUFFICIENT_TIME")
        self.assertEqual(self.verify("software-a", DEVICE_A, token).status_code, 200)

    def test_product_mismatch_and_expired(self):
        key = self.generate(self.create_product("software-a"))[0]
        self.create_product("software-b")
        self.assertEqual(self.activate("software-b", key, DEVICE_A).json()["error"]["code"], "KEY_PRODUCT_MISMATCH")
        self.activate("software-a", key, DEVICE_A)
        LicenseKey.objects.filter(key=key).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.activate("software-a", key, DEVICE_A).json()["error"]["code"], "KEY_EXPIRED")

    def test_export_then_import_to_other_product_rejected(self):
        product_a = self.create_product("software-a")
        product_b = self.create_product("software-b")
        self.generate(product_a, count=10)
        upload = SimpleUploadedFile("keys.csv", self.export(product_a), content_type="text/csv")
        resp = self.console.post(
            reverse("console:key_import", args=[product_b.pk]), {"file": upload, "duration_days": 30}
        )
        self.assertContains(resp, "有 10 个重复的 key")
        self.assertEqual(LicenseKey.objects.filter(product=product_b).count(), 0)
