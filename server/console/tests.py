from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activation import services as activation_services
from keys import services as keys_services
from redeem import services as redeem_services

PASSWORD = "correct-horse-battery"
DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


def activate(product, code, device=DEVICE_B, device_info="pc-b"):
    activation_services.activate(code, device, device_info)
    return next(a for a in activation_services.search(product.code, code))


def make_activation(product, device=DEVICE_B, device_info="pc-b"):
    _, (code,) = keys_services.issue_codes(product.id, 30, 1)
    return activate(product, code, device, device_info)


class ConsoleTestCase(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", PASSWORD)
        self.product = keys_services.create_product("software-a", "软件A")

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def url(self, name, *args):
        return reverse(f"console:{name}", args=args)


class AccessTests(ConsoleTestCase):
    def attempt(self, password):
        return self.client.post(self.url("login"), {"username": "admin", "password": password})

    def test_anonymous_redirected_to_login(self):
        activation = make_activation(self.product)
        batch, _ = keys_services.issue_codes(self.product.id, 30, 1)
        for url in (
            self.url("products"),
            self.url("product_create"),
            self.url("keys", self.product.id),
            self.url("key_export", self.product.id),
            self.url("batches", self.product.id),
            self.url("activation_detail", activation.id),
            self.url("password"),
            self.url("mcp_setup"),
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertTrue(resp["Location"].startswith(self.url("login")), url)
        resp = self.client.post(self.url("batch_disable", batch.batch_id))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(keys_services.list_batches(self.product.id)[0].disabled)

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
        keys_services.update_product(self.product.id, "软件A", True, 24, False)
        make_activation(self.product)
        resp = self.client.get(self.url("products"))
        self.assertContains(resp, "软件A")
        self.assertContains(resp, "允许换设备，每次扣 24 小时")
        self.assertContains(resp, "<td>1</td>", html=True)

    def test_create_product(self):
        resp = self.client.post(
            self.url("product_create"),
            {
                "code": "software-b",
                "name": "软件B",
                "kind": "device",
                "allow_transfer": "on",
                "transfer_penalty_hours": 1,
            },
        )
        product = keys_services.get_product_by_code("software-b")
        self.assertRedirects(resp, self.url("keys", product.id))
        self.assertTrue(product.allow_transfer)
        self.assertEqual(product.kind, "device")

    def test_create_invalid_shows_error(self):
        resp = self.client.post(
            self.url("product_create"),
            {"code": "software-a", "name": "重复", "kind": "device", "transfer_penalty_hours": 0},
        )
        self.assertContains(resp, "该标识已被其它软件使用")
        resp = self.client.post(
            self.url("product_create"), {"code": "bad code", "name": "x", "kind": "device", "transfer_penalty_hours": 0}
        )
        self.assertContains(resp, "标识只能用字母、数字、- 和 _")

    def test_delete_unused_product(self):
        keys_services.issue_codes(self.product.id, 30, 2)
        delete_url = self.url("product_delete", self.product.id)
        self.assertContains(self.client.get(self.url("products")), delete_url)
        resp = self.client.post(delete_url, follow=True)
        self.assertRedirects(resp, self.url("products"))
        self.assertContains(resp, "已删除 软件A（software-a）")
        self.assertEqual(keys_services.list_products(), [])
        keys_services.create_product("software-a", "软件A")

    def test_active_product_cannot_be_deleted(self):
        make_activation(self.product)
        delete_url = self.url("product_delete", self.product.id)
        self.assertNotContains(self.client.get(self.url("products")), delete_url)
        resp = self.client.post(delete_url, follow=True)
        self.assertContains(resp, "还有使用中的卡密，不能删除")
        self.assertEqual(keys_services.get_product(self.product.id).code, "software-a")

    def test_expired_or_disabled_activations_can_be_deleted(self):
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=31)):
            make_activation(self.product, DEVICE_A, "pc-a")
        disabled = make_activation(self.product, DEVICE_B, "pc-b")
        activation_services.toggle_disabled(disabled.id)
        self.assertContains(self.client.get(self.url("products")), self.url("product_delete", self.product.id))
        self.client.post(self.url("product_delete", self.product.id))
        self.assertEqual(keys_services.list_products(), [])
        self.assertEqual(activation_services.count_by_product(), {})

    def test_edit_cannot_change_code(self):
        self.client.post(
            self.url("product_edit", self.product.id),
            {"code": "hacked", "name": "新名字", "transfer_penalty_hours": 0, "disabled": "on"},
        )
        product = keys_services.get_product(self.product.id)
        self.assertEqual((product.code, product.name, product.disabled), ("software-a", "新名字", True))


class CodePageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.other = keys_services.create_product("software-b", "软件B")

    def test_issue_for_current_product_only(self):
        resp = self.client.post(self.url("key_issue", self.product.id), {"count": 50, "duration_days": 30})
        self.assertEqual(resp.status_code, 200)
        (batch,) = keys_services.list_batches(self.product.id)
        self.assertEqual((batch.count, batch.duration_days), (50, 30))
        self.assertEqual(keys_services.list_batches(self.other.id), [])
        first_code = keys_services.export_codes_csv(self.product.id).splitlines()[1].split(",")[0]
        self.assertContains(resp, first_code)
        self.assertEqual(activation_services.count_by_product(), {})

    def test_issue_over_limit_shows_error(self):
        resp = self.client.post(
            self.url("key_issue", self.product.id), {"count": 1001, "duration_days": 30}, follow=True
        )
        self.assertContains(resp, "生成数量必须在 1 到 1000 之间")
        self.assertEqual(keys_services.list_batches(self.product.id), [])

    def test_export_batch_and_all_current_product_only(self):
        first, first_codes = keys_services.issue_codes(self.product.id, 30, 2)
        _, second_codes = keys_services.issue_codes(self.product.id, 7, 1)
        other_batch, other_codes = keys_services.issue_codes(self.other.id, 30, 1)

        resp = self.client.get(self.url("key_export", self.product.id), {"batch": first.batch_id})
        content = resp.content.decode("utf-8-sig")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertTrue(all(c in content for c in first_codes))
        self.assertNotIn(second_codes[0], content)

        content = self.client.get(self.url("key_export", self.product.id)).content.decode("utf-8-sig")
        self.assertTrue(all(c in content for c in first_codes + second_codes))
        self.assertNotIn(other_codes[0], content)

        resp = self.client.get(self.url("key_export", self.product.id), {"batch": other_batch.batch_id})
        self.assertEqual(resp.status_code, 404)

    def test_list_search_and_pagination(self):
        _, codes = keys_services.issue_codes(self.product.id, 30, 60)
        for i, code in enumerate(codes):
            activate(self.product, code, device_info=f"pc-{i:03d}")
        make_activation(self.other, device_info="theirs")
        resp = self.client.get(self.url("keys", self.product.id))
        self.assertContains(resp, "第 1 / 2 页")
        self.assertNotContains(resp, "theirs")
        resp = self.client.get(self.url("keys", self.product.id), {"q": codes[42]})
        self.assertContains(resp, "pc-042")
        self.assertNotContains(resp, "pc-041")
        resp = self.client.get(self.url("keys", self.product.id), {"q": "pc-007"})
        self.assertContains(resp, "pc-007")
        self.assertNotContains(resp, "pc-008")

    def test_test_window_matches_product_kind(self):
        resp = self.client.get(self.url("keys", self.product.id))
        self.assertContains(resp, 'data-api="/api/activate"')
        self.assertContains(resp, 'data-api="/api/verify"')
        self.assertNotContains(resp, 'data-api="/api/redeem"')
        counted = keys_services.create_product("service-c", "代下载", kind="count")
        resp = self.client.get(self.url("keys", counted.id))
        self.assertContains(resp, 'data-api="/api/redeem"')
        self.assertNotContains(resp, 'data-api="/api/activate"')

    def test_toggle_activation(self):
        activation = make_activation(self.product)
        self.client.post(self.url("activation_toggle", activation.id))
        self.assertTrue(activation_services.get_detail(activation.id)[0].disabled)
        resp = self.client.post(self.url("activation_toggle", activation.id), {"next": "https://evil.example.com/"})
        self.assertRedirects(resp, self.url("keys", self.product.id))
        self.assertFalse(activation_services.get_detail(activation.id)[0].disabled)

    def test_detail_shows_records_read_only(self):
        keys_services.update_product(self.product.id, "软件A", True, 24, False)
        _, (code,) = keys_services.issue_codes(self.product.id, 30, 1)
        activate(self.product, code, DEVICE_A, "pc-a")
        activation = activate(self.product, code, DEVICE_B, "pc-b")
        resp = self.client.get(self.url("activation_detail", activation.id))
        self.assertContains(resp, DEVICE_B)
        self.assertContains(resp, "pc-a")
        self.assertContains(resp, "换设备记录（1）")
        self.assertNotContains(resp, 'method="post" action="/activations/')
        self.assertEqual(self.client.get(self.url("activation_detail", 999)).status_code, 404)


class CountProductTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.client.post(
            self.url("product_create"),
            {"code": "service-b", "name": "代下载", "kind": "count", "transfer_penalty_hours": 0},
        )
        self.counted = keys_services.get_product_by_code("service-b")

    def test_issue_with_uses_and_list_redemptions(self):
        self.assertEqual(self.counted.kind, "count")
        resp = self.client.post(self.url("key_issue", self.counted.id), {"count": 2, "uses": 3})
        self.assertEqual(resp.status_code, 200)
        (batch,) = keys_services.list_batches(self.counted.id)
        self.assertEqual((batch.count, batch.duration_days, batch.uses), (2, None, 3))
        resp = self.client.get(self.url("batches", self.counted.id))
        self.assertContains(resp, "3 次")

        codes = [row.split(",")[0] for row in keys_services.export_codes_csv(self.counted.id).splitlines()[1:]]
        redeem_services.redeem(codes[0])
        redeem_services.redeem(codes[0])
        redeem_services.redeem(codes[1])
        resp = self.client.get(self.url("keys", self.counted.id))
        self.assertContains(resp, "核销记录")
        self.assertContains(resp, ">2 / 3<")
        self.assertContains(resp, ">1 / 3<")
        self.assertContains(resp, "共 2 个")
        self.assertContains(resp, self.url("redemption_detail", codes[0].replace("-", "")))
        resp = self.client.get(self.url("keys", self.counted.id), {"q": codes[1]})
        self.assertContains(resp, codes[1].replace("-", ""))
        self.assertNotContains(resp, codes[0].replace("-", ""))

        resp = self.client.get(self.url("products"))
        self.assertContains(resp, "按次数")
        self.assertContains(resp, "<td>3</td>", html=True)

    def test_redemption_detail_read_only(self):
        _, (code,) = keys_services.issue_codes(self.counted.id, None, 1, uses=2)
        redeem_services.redeem(code)
        redeem_services.redeem(code)
        resp = self.client.get(self.url("redemption_detail", code.replace("-", "")))
        self.assertContains(resp, "已用完")
        self.assertContains(resp, "核销记录（2）")
        self.assertContains(resp, ">1 / 2<")
        self.assertContains(resp, ">2 / 2<")
        self.assertNotContains(resp, 'method="post" action="/redemptions/')
        _, (unused,) = keys_services.issue_codes(self.counted.id, None, 1, uses=2)
        self.assertEqual(self.client.get(self.url("redemption_detail", unused.replace("-", ""))).status_code, 404)

    def test_issue_without_uses_shows_error(self):
        resp = self.client.post(self.url("key_issue", self.counted.id), {"count": 1}, follow=True)
        self.assertContains(resp, "可用次数必须是 1 到 10000 之间的整数")
        self.assertEqual(keys_services.list_batches(self.counted.id), [])

    def test_delete_follows_remaining_uses(self):
        _, (code,) = keys_services.issue_codes(self.counted.id, None, 1, uses=2)
        redeem_services.redeem(code)
        self.client.post(self.url("product_delete", self.counted.id))
        self.assertEqual(keys_services.get_product(self.counted.id).code, "service-b")

        redeem_services.redeem(code)
        self.client.post(self.url("product_delete", self.counted.id))
        self.assertEqual([p.code for p in keys_services.list_products()], ["software-a"])
        self.assertEqual(redeem_services.count_by_product(), {})

    def test_create_ignores_transfer_policy(self):
        self.client.post(
            self.url("product_create"),
            {"code": "service-c", "name": "C", "kind": "count", "allow_transfer": "on", "transfer_penalty_hours": 5},
        )
        product = keys_services.get_product_by_code("service-c")
        self.assertEqual((product.allow_transfer, product.transfer_penalty_hours), (False, 0))

    def test_edit_has_no_transfer_policy(self):
        resp = self.client.get(self.url("product_edit", self.counted.id))
        self.assertNotContains(resp, 'name="allow_transfer"')
        self.client.post(self.url("product_edit", self.counted.id), {"name": "新名字", "disabled": "on"})
        product = keys_services.get_product(self.counted.id)
        self.assertEqual((product.name, product.disabled, product.kind), ("新名字", True, "count"))


class BatchPageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_list_and_disable_is_one_way(self):
        batch, _ = keys_services.issue_codes(self.product.id, 30, 5)
        resp = self.client.get(self.url("batches", self.product.id))
        self.assertContains(resp, batch.batch_id)
        self.assertContains(resp, "<td>5</td>", html=True)

        resp = self.client.post(self.url("batch_disable", batch.batch_id))
        self.assertRedirects(resp, self.url("batches", self.product.id))
        self.assertTrue(keys_services.list_batches(self.product.id)[0].disabled)

        self.client.post(self.url("batch_disable", batch.batch_id))
        self.assertTrue(keys_services.list_batches(self.product.id)[0].disabled)
        resp = self.client.get(self.url("batches", self.product.id))
        self.assertNotContains(resp, reverse("console:batch_disable", args=[batch.batch_id]))


@override_settings(ALLOWED_HOSTS=["auth.example.com", "10.0.0.5"])
class McpSetupPageTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_url_follows_request_host(self):
        resp = self.client.get(self.url("mcp_setup"), HTTP_HOST="auth.example.com", HTTP_X_FORWARDED_PROTO="https")
        self.assertContains(resp, "https://auth.example.com/mcp")
        self.assertContains(resp, "mcpServers")
        resp = self.client.get(self.url("mcp_setup"), HTTP_HOST="10.0.0.5:8000")
        self.assertContains(resp, "http://10.0.0.5:8000/mcp")


class SetupTests(TestCase):
    USERNAME = "owner"
    PASSWORD = "s3cret-pass"

    def url(self, name="setup"):
        return reverse(f"console:{name}")

    def test_everything_redirects_to_setup_when_no_admin(self):
        for path in (self.url("login"), "/api/activate", "/api/redeem", "/mcp", "/"):
            resp = self.client.get(path)
            self.assertRedirects(resp, self.url(), msg_prefix=path)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_setup_page_available(self):
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200)

    def test_create_admin_and_login(self):
        resp = self.client.post(
            self.url(),
            {"username": self.USERNAME, "password": self.PASSWORD, "password_confirm": self.PASSWORD},
        )
        self.assertRedirects(resp, self.url("products"))
        user = get_user_model().objects.get(username=self.USERNAME)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_password_mismatch(self):
        resp = self.client.post(
            self.url(),
            {"username": self.USERNAME, "password": self.PASSWORD, "password_confirm": "other"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "两次输入的密码不一致")
        self.assertFalse(get_user_model().objects.exists())

    def test_empty_username(self):
        resp = self.client.post(
            self.url(), {"username": "  ", "password": self.PASSWORD, "password_confirm": self.PASSWORD}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(get_user_model().objects.exists())

    def test_setup_unavailable_after_admin_exists(self):
        get_user_model().objects.create_superuser("admin", password="whatever")
        resp = self.client.get(self.url())
        self.assertRedirects(resp, self.url("login"))
