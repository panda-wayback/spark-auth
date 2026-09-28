from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from keys.models import Activation, LicenseKey, Product, TransferLog

PASSWORD = "correct-horse-battery"


class ConsoleTestCase(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", PASSWORD)
        self.product = Product.objects.create(code="software-a", name="软件A")

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def url(self, name, *args):
        return reverse(f"console:{name}", args=args)


class AccessTests(ConsoleTestCase):
    def attempt(self, password):
        return self.client.post(self.url("login"), {"username": "admin", "password": password})

    def test_anonymous_redirected_to_login(self):
        lic = LicenseKey.objects.create(key="K-1", product=self.product, duration_days=30)
        for url in (
            self.url("products"),
            self.url("product_create"),
            self.url("keys", self.product.pk),
            self.url("key_export", self.product.pk),
            self.url("key_detail", lic.pk),
            self.url("password"),
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertTrue(resp["Location"].startswith(self.url("login")), url)

    def test_non_superuser_denied(self):
        user = get_user_model().objects.create_user("someone", password=PASSWORD)
        self.login(user)
        resp = self.client.get(self.url("products"))
        self.assertEqual(resp.status_code, 302)

    def test_login_success(self):
        resp = self.attempt(PASSWORD)
        self.assertRedirects(resp, self.url("products"))

    def test_locked_after_five_failures(self):
        for _ in range(5):
            self.attempt("wrong")
        resp = self.attempt(PASSWORD)
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, "登录已暂停", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_no_django_admin(self):
        self.login()
        self.assertEqual(self.client.get("/admin/").status_code, 404)


class ProductPageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_list_shows_policy_and_count(self):
        Product.objects.filter(pk=self.product.pk).update(allow_transfer=True, transfer_penalty_hours=24)
        LicenseKey.objects.create(key="K-1", product=self.product, duration_days=30)
        resp = self.client.get(self.url("products"))
        self.assertContains(resp, "软件A")
        self.assertContains(resp, "允许换设备，每次扣 24 小时")
        self.assertContains(resp, "<td>1</td>", html=True)

    def test_create_product(self):
        resp = self.client.post(
            self.url("product_create"),
            {"code": "software-b", "name": "软件B", "allow_transfer": "on", "transfer_penalty_hours": 1},
        )
        product = Product.objects.get(code="software-b")
        self.assertRedirects(resp, self.url("keys", product.pk))
        self.assertTrue(product.allow_transfer)

    def test_edit_cannot_change_code(self):
        self.client.post(
            self.url("product_edit", self.product.pk),
            {"code": "hacked", "name": "新名字", "transfer_penalty_hours": 0, "disabled": "on"},
        )
        self.product.refresh_from_db()
        self.assertEqual((self.product.code, self.product.name, self.product.disabled), ("software-a", "新名字", True))


class KeyPageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.other = Product.objects.create(code="software-b", name="软件B")

    def test_generate_for_current_product_only(self):
        resp = self.client.post(self.url("key_generate", self.product.pk), {"count": 50, "duration_days": 30})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LicenseKey.objects.filter(product=self.product).count(), 50)
        self.assertEqual(LicenseKey.objects.filter(product=self.other).count(), 0)
        self.assertContains(resp, LicenseKey.objects.first().key)

    def test_generate_over_limit_shows_error(self):
        resp = self.client.post(
            self.url("key_generate", self.product.pk), {"count": 1001, "duration_days": 30}, follow=True
        )
        self.assertContains(resp, "生成数量必须在 1 到 1000 之间")
        self.assertEqual(LicenseKey.objects.count(), 0)

    def test_import_duplicate_lists_keys(self):
        LicenseKey.objects.create(key="DUP-1", product=self.other, duration_days=1)
        upload = SimpleUploadedFile("keys.csv", b"NEW-1\nDUP-1\n", content_type="text/csv")
        resp = self.client.post(self.url("key_import", self.product.pk), {"file": upload, "duration_days": 30})
        self.assertContains(resp, "DUP-1")
        self.assertContains(resp, "整批未导入")
        self.assertFalse(LicenseKey.objects.filter(key="NEW-1").exists())

    def test_import_success(self):
        upload = SimpleUploadedFile("keys.csv", b"NEW-1\nNEW-2\n", content_type="text/csv")
        resp = self.client.post(self.url("key_import", self.product.pk), {"file": upload, "duration_days": 30})
        self.assertRedirects(resp, self.url("keys", self.product.pk))
        self.assertEqual(LicenseKey.objects.filter(product=self.product, key__startswith="NEW-").count(), 2)

    def test_export_current_product_only(self):
        LicenseKey.objects.create(key="MINE", product=self.product, duration_days=30)
        LicenseKey.objects.create(key="THEIRS", product=self.other, duration_days=30)
        resp = self.client.get(self.url("key_export", self.product.pk))
        content = resp.content.decode("utf-8-sig")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("MINE", content)
        self.assertNotIn("THEIRS", content)

    def test_list_search_and_pagination(self):
        for i in range(60):
            LicenseKey.objects.create(key=f"K-{i:03d}", product=self.product, duration_days=30)
        resp = self.client.get(self.url("keys", self.product.pk))
        self.assertContains(resp, "第 1 / 2 页")
        resp = self.client.get(self.url("keys", self.product.pk), {"q": "K-042"})
        self.assertContains(resp, "K-042")
        self.assertNotContains(resp, "K-041")

    def test_toggle_disable(self):
        lic = LicenseKey.objects.create(key="K-1", product=self.product, duration_days=30)
        self.client.post(self.url("key_toggle", lic.pk))
        lic.refresh_from_db()
        self.assertTrue(lic.disabled)
        resp = self.client.post(self.url("key_toggle", lic.pk), {"next": "https://evil.example.com/"})
        self.assertRedirects(resp, self.url("keys", self.product.pk))
        lic.refresh_from_db()
        self.assertFalse(lic.disabled)

    def test_detail_shows_records_read_only(self):
        lic = LicenseKey.objects.create(
            key="K-1", product=self.product, duration_days=30, expires_at=timezone.now()
        )
        Activation.objects.create(key=lic, device_hash="b" * 64, device_info="pc-b", activated_at=timezone.now())
        TransferLog.objects.create(
            key=lic,
            old_device_hash="a" * 64,
            old_device_info="pc-a",
            new_device_hash="b" * 64,
            new_device_info="pc-b",
            penalty_hours=24,
            expires_before=timezone.now(),
            expires_after=timezone.now(),
        )
        resp = self.client.get(self.url("key_detail", lic.pk))
        self.assertContains(resp, "b" * 64)
        self.assertContains(resp, "pc-a")
        self.assertContains(resp, "换设备记录（1）")
        self.assertNotContains(resp, "<form method=\"post\" action=\"/keys/")
