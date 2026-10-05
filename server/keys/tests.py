import csv
import io

from django.test import TestCase, override_settings

from . import codes, services
from .errors import ServiceError
from .models import Batch


class KeysTestCase(TestCase):
    def assertCode(self, code, func, *args, **kwargs):
        with self.assertRaises(ServiceError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)


class ProductTests(KeysTestCase):
    def test_create_and_get(self):
        product = services.create_product("software-a", "软件A", True, 24)
        self.assertEqual(services.get_product(product.id), product)
        self.assertEqual(services.get_product_by_code("software-a"), product)
        self.assertEqual(product.transfer_policy_label, "允许换设备，每次扣 24 小时")
        self.assertEqual(services.list_products(), [product])

    def test_create_invalid(self):
        services.create_product("software-a", "A")
        self.assertCode("PRODUCT_EXISTS", services.create_product, "software-a", "B")
        for code, name in (("bad code", "A"), ("", "A"), ("x" * 65, "A"), ("ok", ""), ("ok", "x" * 129)):
            self.assertCode("REQUEST_INVALID", services.create_product, code, name)
        self.assertCode("REQUEST_INVALID", services.create_product, "ok", "A", False, -1)

    def test_update_keeps_code(self):
        product = services.create_product("software-a", "A")
        updated = services.update_product(product.id, "新名字", True, 0, True)
        self.assertEqual(
            (updated.code, updated.name, updated.allow_transfer, updated.disabled), ("software-a", "新名字", True, True)
        )
        self.assertEqual(services.get_product_by_code("software-a"), updated)

    def test_not_found(self):
        self.assertCode("PRODUCT_NOT_FOUND", services.get_product, 999)
        self.assertCode("PRODUCT_NOT_FOUND", services.get_product_by_code, "nope")


class IssueTests(KeysTestCase):
    def setUp(self):
        self.product = services.create_product("software-a", "A")

    def test_single_and_max_batch(self):
        _, one = services.issue_codes(self.product.id, 30, 1)
        self.assertEqual(len(one), 1)
        batch, many = services.issue_codes(self.product.id, 30, 1000)
        self.assertEqual(len(set(many)), 1000)
        self.assertTrue(all(len(codes.normalize(c)) == 28 for c in many))
        self.assertEqual(batch.count, 1000)
        self.assertEqual(len(services.list_batches(self.product.id)), 2)

    def test_invalid_counts_and_duration(self):
        for count in (0, 1001):
            self.assertCode("BATCH_COUNT_INVALID", services.issue_codes, self.product.id, 30, count)
        for days in (0, 36501):
            self.assertCode("REQUEST_INVALID", services.issue_codes, self.product.id, days, 1)
        self.assertEqual(services.list_batches(self.product.id), [])

    def test_disable_is_one_way(self):
        batch, _ = services.issue_codes(self.product.id, 30, 1)
        self.assertTrue(services.disable_batch(batch.batch_id).disabled)
        self.assertTrue(services.disable_batch(batch.batch_id).disabled)
        self.assertCode("BATCH_NOT_FOUND", services.disable_batch, "0" * 10)


class CheckCodeTests(KeysTestCase):
    def setUp(self):
        self.product = services.create_product("software-a", "A")
        self.batch, self.codes = services.issue_codes(self.product.id, 7, 2)

    def test_returns_batch(self):
        batch = services.check_code("software-a", self.codes[1])
        self.assertEqual((batch.batch_id, batch.duration_days, batch.product_code), (self.batch.batch_id, 7, "software-a"))

    def test_input_is_normalized(self):
        messy = "  " + self.codes[0].lower().replace("-", " - ") + "\n"
        self.assertEqual(codes.normalize(messy), codes.normalize(self.codes[0]))
        services.check_code("software-a", messy)

    def test_tampered_or_malformed(self):
        code = codes.normalize(self.codes[0])
        tampered = code[:20] + ("A" if code[20] != "A" else "B") + code[21:]
        for bad in (tampered, code[:-1], "NOT A CODE!", ""):
            self.assertCode("CODE_INVALID", services.check_code, "software-a", bad)

    def test_secret_key_rotation_invalidates(self):
        with override_settings(SECRET_KEY="another-secret"):
            self.assertCode("CODE_INVALID", services.check_code, "software-a", self.codes[0])

    def test_batch_secret_required(self):
        Batch.objects.filter(batch_id=self.batch.batch_id).update(secret=codes.new_batch_secret())
        self.assertCode("CODE_INVALID", services.check_code, "software-a", self.codes[0])

    def test_unknown_batch_and_serial_out_of_range(self):
        secret = Batch.objects.get(batch_id=self.batch.batch_id).secret
        for bad in (
            codes.sign("0" * 10, secret, 1),
            codes.sign(self.batch.batch_id, secret, 0),
            codes.sign(self.batch.batch_id, secret, 3),
        ):
            self.assertCode("CODE_INVALID", services.check_code, "software-a", bad)
        services.check_code("software-a", codes.sign(self.batch.batch_id, secret, 2))

    def test_product_mismatch_and_disabled_batch(self):
        services.create_product("software-b", "B")
        self.assertCode("CODE_PRODUCT_MISMATCH", services.check_code, "software-b", self.codes[0])
        services.disable_batch(self.batch.batch_id)
        self.assertCode("BATCH_DISABLED", services.check_code, "software-a", self.codes[0])


class ExportTests(KeysTestCase):
    def rows(self, *args):
        return list(csv.reader(io.StringIO(services.export_codes_csv(*args))))

    def test_export_matches_issued_codes(self):
        product = services.create_product("software-a", "A")
        first, first_codes = services.issue_codes(product.id, 30, 3)
        _, second_codes = services.issue_codes(product.id, 7, 2)

        rows = self.rows(product.id, first.batch_id)
        self.assertEqual(rows[0], services.EXPORT_HEADER)
        self.assertEqual([r[0] for r in rows[1:]], first_codes)
        self.assertEqual(rows[1][1], "30")

        rows = self.rows(product.id)
        self.assertEqual([r[0] for r in rows[1:]], first_codes + second_codes)

    def test_other_product_batch_not_found(self):
        product = services.create_product("software-a", "A")
        other = services.create_product("software-b", "B")
        batch, _ = services.issue_codes(other.id, 30, 1)
        self.assertCode("BATCH_NOT_FOUND", services.export_codes_csv, product.id, batch.batch_id)
