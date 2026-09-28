import csv
import io
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from . import services
from .errors import ServiceError
from .models import Activation, LicenseKey, Product, TransferLog


def make_product(code="software-a", **kwargs):
    return Product.objects.create(code=code, name=code, **kwargs)


class ConstraintTests(TestCase):
    def test_product_code_unique(self):
        make_product()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_product()

    def test_key_unique(self):
        product = make_product()
        LicenseKey.objects.create(key="K1", product=product, duration_days=30)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LicenseKey.objects.create(key="K1", product=product, duration_days=30)

    def test_one_activation_per_key(self):
        lic = LicenseKey.objects.create(key="K1", product=make_product(), duration_days=30)
        Activation.objects.create(key=lic, device_hash="a" * 64, activated_at=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Activation.objects.create(key=lic, device_hash="b" * 64, activated_at=timezone.now())

    def test_transfer_log_append_only(self):
        lic = LicenseKey.objects.create(key="K1", product=make_product(), duration_days=30)
        now = timezone.now()
        log = TransferLog.objects.create(
            key=lic,
            old_device_hash="a" * 64,
            new_device_hash="b" * 64,
            penalty_hours=1,
            expires_before=now,
            expires_after=now - timedelta(hours=1),
        )
        with self.assertRaises(PermissionError):
            log.save()
        with self.assertRaises(PermissionError):
            log.delete()


class GenerateTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_single_and_max_batch(self):
        self.assertEqual(len(services.generate_keys(self.product, 30, 1)), 1)
        created = services.generate_keys(self.product, 30, 1000)
        self.assertEqual(len(created), 1000)
        self.assertEqual(LicenseKey.objects.count(), 1001)

    def test_invalid_counts(self):
        for count in (0, 1001):
            with self.assertRaises(ServiceError) as ctx:
                services.generate_keys(self.product, 30, count)
            self.assertEqual(ctx.exception.code, "BATCH_COUNT_INVALID")
        self.assertEqual(LicenseKey.objects.count(), 0)

    def test_generated_keys_are_inactive(self):
        lic = services.generate_keys(self.product, 30, 1)[0]
        lic.refresh_from_db()
        self.assertIsNone(lic.expires_at)
        self.assertEqual(lic.status, LicenseKey.STATUS_INACTIVE)


class ImportTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_import_success_with_header_and_bom(self):
        created = services.import_keys(self.product, 7, "\ufeffkey\nAAA\nBBB\n\n".encode("utf-8"))
        self.assertEqual(sorted(k.key for k in created), ["AAA", "BBB"])
        self.assertTrue(all(k.duration_days == 7 for k in LicenseKey.objects.all()))

    def test_duplicate_in_file_rejects_whole_batch(self):
        with self.assertRaises(ServiceError) as ctx:
            services.import_keys(self.product, 7, "AAA\nBBB\nAAA\n")
        self.assertEqual(ctx.exception.code, "IMPORT_DUPLICATE")
        self.assertEqual(ctx.exception.details, ["AAA"])
        self.assertEqual(LicenseKey.objects.count(), 0)

    def test_duplicate_with_existing_rejects_whole_batch(self):
        LicenseKey.objects.create(key="BBB", product=make_product("software-b"), duration_days=1)
        with self.assertRaises(ServiceError) as ctx:
            services.import_keys(self.product, 7, "AAA\nBBB\n")
        self.assertEqual(ctx.exception.code, "IMPORT_DUPLICATE")
        self.assertEqual(ctx.exception.details, ["BBB"])
        self.assertEqual(LicenseKey.objects.count(), 1)

    def test_invalid_files(self):
        for content in ("AAA\n,x\n", "", b"\xff\xfe\x00", "key\n"):
            with self.assertRaises(ServiceError) as ctx:
                services.import_keys(self.product, 7, content)
            self.assertEqual(ctx.exception.code, "IMPORT_INVALID")
        self.assertEqual(LicenseKey.objects.count(), 0)


class ExportTests(TestCase):
    def test_export_columns(self):
        product = make_product()
        inactive = LicenseKey.objects.create(key="K-INACTIVE", product=product, duration_days=30)
        active = LicenseKey.objects.create(
            key="K-ACTIVE", product=product, duration_days=30, expires_at=timezone.now() + timedelta(days=3)
        )
        Activation.objects.create(key=active, device_hash="c" * 64, device_info="pc-1", activated_at=timezone.now())
        LicenseKey.objects.create(key="OTHER", product=make_product("software-b"), duration_days=30)

        rows = list(csv.reader(io.StringIO(services.export_keys_csv(product))))
        self.assertEqual(rows[0], services.EXPORT_HEADER)
        by_key = {row[0]: row for row in rows[1:]}
        self.assertEqual(set(by_key), {inactive.key, active.key})
        self.assertEqual(by_key["K-INACTIVE"], ["K-INACTIVE", "未激活", "30", "", "", "0"])
        self.assertEqual(by_key["K-ACTIVE"][1], "有效")
        self.assertNotEqual(by_key["K-ACTIVE"][3], "")
        self.assertIn("c" * 64, by_key["K-ACTIVE"][4])
