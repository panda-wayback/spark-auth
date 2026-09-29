from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from keys import services as keys_services
from keys.models import Activation, Product, SigningKey, TransferLog

PASSWORD = "correct-horse-battery"


def make_activation(product, code, device="b" * 64, device_info="pc-b"):
    return Activation.objects.create(
        code=code,
        product=product,
        device_hash=device,
        device_info=device_info,
        duration_days=30,
        expires_at=timezone.now() + timedelta(days=30),
        activated_at=timezone.now(),
    )


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
        activation = make_activation(self.product, "C1")
        signing_key, _ = keys_services.issue_codes(self.product, 30, 1)
        for url in (
            self.url("products"),
            self.url("product_create"),
            self.url("keys", self.product.pk),
            self.url("key_export", self.product.pk),
            self.url("signing_keys", self.product.pk),
            self.url("activation_detail", activation.pk),
            self.url("password"),
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertTrue(resp["Location"].startswith(self.url("login")), url)
        resp = self.client.post(self.url("signing_key_disable", signing_key.pk))
        self.assertEqual(resp.status_code, 302)
        signing_key.refresh_from_db()
        self.assertFalse(signing_key.disabled)

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

    def test_list_shows_policy_and_active_count(self):
        Product.objects.filter(pk=self.product.pk).update(allow_transfer=True, transfer_penalty_hours=24)
        make_activation(self.product, "C1")
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


class CodePageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.other = Product.objects.create(code="software-b", name="软件B")

    def test_issue_for_current_product_only(self):
        resp = self.client.post(self.url("key_issue", self.product.pk), {"count": 50, "duration_days": 30})
        self.assertEqual(resp.status_code, 200)
        signing_key = SigningKey.objects.get()
        self.assertEqual((signing_key.product, signing_key.count, signing_key.duration_days), (self.product, 50, 30))
        self.assertContains(resp, keys_services.batch_codes(signing_key)[0])
        self.assertEqual(Activation.objects.count(), 0)

    def test_issue_over_limit_shows_error(self):
        resp = self.client.post(
            self.url("key_issue", self.product.pk), {"count": 1001, "duration_days": 30}, follow=True
        )
        self.assertContains(resp, "生成数量必须在 1 到 1000 之间")
        self.assertEqual(SigningKey.objects.count(), 0)

    def test_export_batch_and_all_current_product_only(self):
        first, first_codes = keys_services.issue_codes(self.product, 30, 2)
        _, second_codes = keys_services.issue_codes(self.product, 7, 1)
        _, other_codes = keys_services.issue_codes(self.other, 30, 1)

        resp = self.client.get(self.url("key_export", self.product.pk), {"batch": first.key_id})
        content = resp.content.decode("utf-8-sig")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertTrue(all(c in content for c in first_codes))
        self.assertNotIn(second_codes[0], content)

        content = self.client.get(self.url("key_export", self.product.pk)).content.decode("utf-8-sig")
        self.assertTrue(all(c in content for c in first_codes + second_codes))
        self.assertNotIn(other_codes[0], content)

        other_batch = SigningKey.objects.get(product=self.other).key_id
        resp = self.client.get(self.url("key_export", self.product.pk), {"batch": other_batch})
        self.assertEqual(resp.status_code, 404)

    def test_list_search_and_pagination(self):
        for i in range(60):
            make_activation(self.product, f"CODE{i:03d}", device_info=f"pc-{i:03d}")
        make_activation(self.other, "THEIRS")
        resp = self.client.get(self.url("keys", self.product.pk))
        self.assertContains(resp, "第 1 / 2 页")
        self.assertNotContains(resp, "THEIRS")
        resp = self.client.get(self.url("keys", self.product.pk), {"q": "CODE042"})
        self.assertContains(resp, "CODE042")
        self.assertNotContains(resp, "CODE041")
        resp = self.client.get(self.url("keys", self.product.pk), {"q": "pc-007"})
        self.assertContains(resp, "CODE007")
        self.assertNotContains(resp, "CODE008")

    def test_toggle_activation(self):
        activation = make_activation(self.product, "C1")
        self.client.post(self.url("activation_toggle", activation.pk))
        activation.refresh_from_db()
        self.assertTrue(activation.disabled)
        resp = self.client.post(self.url("activation_toggle", activation.pk), {"next": "https://evil.example.com/"})
        self.assertRedirects(resp, self.url("keys", self.product.pk))
        activation.refresh_from_db()
        self.assertFalse(activation.disabled)

    def test_detail_shows_records_read_only(self):
        activation = make_activation(self.product, "C1")
        TransferLog.objects.create(
            activation=activation,
            old_device_hash="a" * 64,
            old_device_info="pc-a",
            new_device_hash="b" * 64,
            new_device_info="pc-b",
            penalty_hours=24,
            expires_before=timezone.now(),
            expires_after=timezone.now(),
        )
        resp = self.client.get(self.url("activation_detail", activation.pk))
        self.assertContains(resp, "b" * 64)
        self.assertContains(resp, "pc-a")
        self.assertContains(resp, "换设备记录（1）")
        self.assertNotContains(resp, 'method="post" action="/activations/')


class SigningKeyPageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_list_and_disable_is_one_way(self):
        signing_key, _ = keys_services.issue_codes(self.product, 30, 5)
        resp = self.client.get(self.url("signing_keys", self.product.pk))
        self.assertContains(resp, signing_key.key_id)
        self.assertContains(resp, "<td>5</td>", html=True)

        resp = self.client.post(self.url("signing_key_disable", signing_key.pk))
        self.assertRedirects(resp, self.url("signing_keys", self.product.pk))
        signing_key.refresh_from_db()
        self.assertTrue(signing_key.disabled)

        self.client.post(self.url("signing_key_disable", signing_key.pk))
        signing_key.refresh_from_db()
        self.assertTrue(signing_key.disabled)
        resp = self.client.get(self.url("signing_keys", self.product.pk))
        self.assertNotContains(resp, reverse("console:signing_key_disable", args=[signing_key.pk]))
