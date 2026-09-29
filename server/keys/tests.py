import csv
import io
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from . import codes, services
from .errors import ServiceError
from .models import Activation, Product, SigningKey, TransferLog


def make_product(code="software-a", **kwargs):
    return Product.objects.create(code=code, name=code, **kwargs)


def make_activation(product, code="C1", device="a" * 64):
    return Activation.objects.create(
        code=code,
        product=product,
        device_hash=device,
        duration_days=30,
        expires_at=timezone.now() + timedelta(days=30),
        activated_at=timezone.now(),
    )


class ConstraintTests(TestCase):
    def test_product_code_unique(self):
        make_product()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_product()

    def test_one_activation_per_code(self):
        product = make_product()
        make_activation(product)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_activation(product, device="b" * 64)

    def test_transfer_log_append_only(self):
        now = timezone.now()
        log = TransferLog.objects.create(
            activation=make_activation(make_product()),
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


class IssueTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_single_and_max_batch(self):
        _, one = services.issue_codes(self.product, 30, 1)
        self.assertEqual(len(one), 1)
        signing_key, many = services.issue_codes(self.product, 30, 1000)
        self.assertEqual(len(set(many)), 1000)
        self.assertEqual(signing_key.count, 1000)
        self.assertEqual(SigningKey.objects.count(), 2)

    def test_invalid_counts(self):
        for count in (0, 1001):
            with self.assertRaises(ServiceError) as ctx:
                services.issue_codes(self.product, 30, count)
            self.assertEqual(ctx.exception.code, "BATCH_COUNT_INVALID")
        self.assertEqual(SigningKey.objects.count(), 0)

    def test_invalid_duration(self):
        for days in (0, 36501):
            with self.assertRaises(ServiceError) as ctx:
                services.issue_codes(self.product, days, 1)
            self.assertEqual(ctx.exception.code, "REQUEST_INVALID")

    def test_each_batch_has_its_own_key(self):
        first, _ = services.issue_codes(self.product, 30, 1)
        second, _ = services.issue_codes(self.product, 30, 1)
        self.assertNotEqual(first.key_id, second.key_id)
        self.assertNotEqual(first.private_pem, second.private_pem)


class CodeTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.signing_key, self.codes = services.issue_codes(self.product, 7, 2)

    def test_verify_reads_payload(self):
        code = self.codes[1]
        self.assertEqual(codes.read_key_id(code), self.signing_key.key_id)
        info = codes.verify_code(self.signing_key.private_pem, code)
        self.assertEqual(
            (info.key_id, info.product_code, info.duration_days, info.serial),
            (self.signing_key.key_id, "software-a", 7, 2),
        )
        self.assertEqual(info.issued_at, int(self.signing_key.created_at.timestamp()))

    def test_input_is_normalized(self):
        code = self.codes[0]
        messy = "  " + "-".join(code[i : i + 5] for i in range(0, len(code), 5)).lower() + "\n"
        self.assertEqual(codes.normalize(messy), code)
        codes.verify_code(self.signing_key.private_pem, messy)

    def test_tampered_or_foreign_code_rejected(self):
        code = self.codes[0]
        tampered = code[:20] + ("A" if code[20] != "A" else "B") + code[21:]
        other, _ = services.issue_codes(self.product, 7, 1)
        for bad, pem in ((tampered, self.signing_key.private_pem), (code, other.private_pem), ("NOT A CODE!", other.private_pem)):
            with self.assertRaises(ServiceError) as ctx:
                codes.verify_code(pem, bad)
            self.assertEqual(ctx.exception.code, "CODE_INVALID")


class ExportTests(TestCase):
    def test_export_matches_issued_codes(self):
        product = make_product()
        first, first_codes = services.issue_codes(product, 30, 3)
        second, second_codes = services.issue_codes(product, 7, 2)

        rows = list(csv.reader(io.StringIO(services.export_codes_csv([first]))))
        self.assertEqual(rows[0], services.EXPORT_HEADER)
        self.assertEqual([r[0] for r in rows[1:]], first_codes)
        self.assertEqual(rows[1][1], "30")

        rows = list(csv.reader(io.StringIO(services.export_codes_csv([first, second]))))
        self.assertEqual([r[0] for r in rows[1:]], first_codes + second_codes)
