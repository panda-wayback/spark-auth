import csv
import io
import json
import os
import stat
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from activation import services as activation_services
from config.secret import load_secret_key
from keys import services as keys_services

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


def later(**delta):
    return patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(**delta))


class EndToEndTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-123456")
        self.console = Client()
        self.console.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        self.app = Client()

    def create_product(self, code, allow_transfer=False, penalty_hours=0, kind="device"):
        resp = self.console.post(
            reverse("console:product_create"),
            {
                "code": code,
                "name": code,
                "kind": kind,
                "allow_transfer": "on" if allow_transfer else "",
                "transfer_penalty_hours": penalty_hours,
            },
        )
        self.assertEqual(resp.status_code, 302)
        return keys_services.get_product_by_code(code)

    def export(self, product, batch=""):
        resp = self.console.get(reverse("console:key_export", args=[product.id]), {"batch": batch} if batch else {})
        return [row[0] for row in csv.reader(io.StringIO(resp.content.decode("utf-8-sig")))][1:]

    def issue(self, product, count=1, days=30):
        resp = self.console.post(reverse("console:key_issue", args=[product.id]), {"count": count, "duration_days": days})
        self.assertEqual(resp.status_code, 200)
        return self.export(product, keys_services.list_batches(product.id)[0].batch_id)

    def call(self, endpoint, **payload):
        return self.app.post(f"/api/{endpoint}", data=json.dumps(payload), content_type="application/json")

    def activate(self, code, device):
        return self.call("activate", code=code, device_hash=device)

    def verify(self, device, token):
        return self.call("verify", device_hash=device, token=token)

    def test_activate_and_verify(self):
        code = self.issue(self.create_product("software-a"))[0]
        resp = self.activate(code, DEVICE_A)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.verify(DEVICE_A, resp.json()["token"]).status_code, 200)

    def test_batch_export_then_activate(self):
        product = self.create_product("software-a")
        codes = self.issue(product, count=20)
        self.assertEqual(len(set(codes)), 20)
        self.assertEqual(activation_services.count_by_product(), {})
        self.assertEqual(self.activate(codes[5], DEVICE_A).status_code, 200)

    def test_transfer_not_allowed(self):
        code = self.issue(self.create_product("software-a", allow_transfer=False))[0]
        self.activate(code, DEVICE_A)
        resp = self.activate(code, DEVICE_B)
        self.assertEqual(resp.json()["error"]["code"], "TRANSFER_NOT_ALLOWED")

    def test_transfer_deducts_one_day(self):
        code = self.issue(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        first = self.activate(code, DEVICE_A).json()
        second = self.activate(code, DEVICE_B).json()
        delta = parse_datetime(first["expires_at"]) - parse_datetime(second["expires_at"])
        self.assertEqual(delta, timedelta(days=1))
        (activation,) = activation_services.search("software-a", code)
        self.assertEqual(activation.transfer_count, 1)
        resp = self.console.get(reverse("console:activation_detail", args=[activation.id]))
        self.assertContains(resp, "换设备记录（1）")
        resp = self.verify(DEVICE_A, first["token"])
        self.assertEqual(resp.json()["error"]["code"], "DEVICE_MISMATCH")

    def test_transfer_insufficient_time_keeps_old_device(self):
        code = self.issue(self.create_product("software-a", allow_transfer=True, penalty_hours=24))[0]
        token = self.activate(code, DEVICE_A).json()["token"]
        with later(days=29, hours=22):
            resp = self.activate(code, DEVICE_B)
            self.assertEqual(resp.json()["error"]["code"], "TRANSFER_INSUFFICIENT_TIME")
            self.assertEqual(self.verify(DEVICE_A, token).status_code, 200)

    def test_expired(self):
        code = self.issue(self.create_product("software-a"))[0]
        self.activate(code, DEVICE_A)
        with later(days=30, minutes=1):
            self.assertEqual(self.activate(code, DEVICE_A).json()["error"]["code"], "CODE_EXPIRED")

    def test_disable_batch_keeps_activated_devices(self):
        product = self.create_product("software-a")
        activated, unused = self.issue(product, count=2)
        token = self.activate(activated, DEVICE_A).json()["token"]
        batch_id = keys_services.list_batches(product.id)[0].batch_id
        self.console.post(reverse("console:batch_disable", args=[batch_id]))
        self.assertEqual(self.activate(unused, DEVICE_B).json()["error"]["code"], "BATCH_DISABLED")
        self.assertEqual(self.verify(DEVICE_A, token).status_code, 200)

    def test_count_code_redeemed_until_used_up(self):
        product = self.create_product("download-service", kind="count")
        resp = self.console.post(reverse("console:key_issue", args=[product.id]), {"count": 1, "uses": 2})
        self.assertEqual(resp.status_code, 200)
        code = self.export(product)[0]
        for remaining in (1, 0):
            resp = self.call("redeem", code=code)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["remaining"], remaining)
        self.assertEqual(self.call("redeem", code=code).json()["error"]["code"], "CODE_USED")
        self.assertEqual(self.activate(code, DEVICE_A).json()["error"]["code"], "CODE_TYPE_MISMATCH")
        resp = self.console.get(reverse("console:keys", args=[product.id]))
        self.assertContains(resp, code.replace("-", ""))

    def test_ip_from_proxy_shown_in_console(self):
        device_code = self.issue(self.create_product("software-a", allow_transfer=True))[0]
        self.app.post(
            "/api/activate",
            data=json.dumps({"code": device_code, "device_hash": DEVICE_A}),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="10.0.0.9, 203.0.113.7",
        )
        (activation,) = activation_services.search("software-a")
        self.assertContains(self.console.get(reverse("console:activation_detail", args=[activation.id])), "203.0.113.7")

        product = self.create_product("download-service", kind="count")
        self.console.post(reverse("console:key_issue", args=[product.id]), {"count": 1, "uses": 2})
        code = self.export(product)[0]
        for forwarded in ("198.51.100.5", "not-an-ip"):
            self.app.post(
                "/api/redeem",
                data=json.dumps({"code": code}),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR=forwarded,
            )
        resp = self.console.get(reverse("console:redemption_detail", args=[code.replace("-", "")]))
        self.assertContains(resp, "198.51.100.5")
        self.assertContains(resp, ">127.0.0.1<")


class HealthTests(TestCase):
    def test_ok_without_admin(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_database_unavailable(self):
        with patch.object(connection, "cursor", side_effect=DatabaseError):
            resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"ok": False})


class SecretKeyTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.dir.name) / "data" / "db.sqlite3"
        self.secret_file = self.db_path.parent / "secret_key"

    def tearDown(self):
        self.dir.cleanup()

    def test_generated_once_and_reused(self):
        with mock.patch.dict(os.environ, {"SPARK_AUTH_SECRET_KEY": ""}):
            first = load_secret_key(self.db_path)
            second = load_secret_key(self.db_path)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 50)
        self.assertEqual(stat.S_IMODE(self.secret_file.stat().st_mode), 0o600)

    def test_env_takes_precedence(self):
        with mock.patch.dict(os.environ, {"SPARK_AUTH_SECRET_KEY": "from-env"}):
            self.assertEqual(load_secret_key(self.db_path), "from-env")
        self.assertFalse(self.secret_file.exists())
