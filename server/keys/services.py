import csv
import io
import secrets

from django.db import transaction
from django.utils import timezone

from .errors import ServiceError
from .models import LicenseKey

BATCH_MAX = 1000
KEY_MAX_LENGTH = LicenseKey._meta.get_field("key").max_length

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GROUPS = 5
_GROUP_SIZE = 5

EXPORT_HEADER = ["key", "状态", "有效时长（天）", "到期时间", "当前绑定设备", "换设备次数"]


def _random_key():
    groups = (
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_SIZE)) for _ in range(_GROUPS)
    )
    return "-".join(groups)


def _check_duration(duration_days):
    if not isinstance(duration_days, int) or duration_days < 1:
        raise ServiceError("REQUEST_INVALID", "有效时长必须是不小于 1 的整数（天）")


def generate_keys(product, duration_days, count=1):
    if not isinstance(count, int) or not 1 <= count <= BATCH_MAX:
        raise ServiceError("BATCH_COUNT_INVALID", f"生成数量必须在 1 到 {BATCH_MAX} 之间")
    _check_duration(duration_days)

    with transaction.atomic():
        candidates = set()
        while len(candidates) < count:
            needed = count - len(candidates)
            batch = {_random_key() for _ in range(needed)} - candidates
            taken = set(LicenseKey.objects.filter(key__in=batch).values_list("key", flat=True))
            candidates |= batch - taken
        return LicenseKey.objects.bulk_create(
            LicenseKey(key=k, product=product, duration_days=duration_days) for k in candidates
        )


def parse_import_csv(content):
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ServiceError("IMPORT_INVALID", "导入文件必须是 UTF-8 编码的 CSV")
    else:
        content = content.lstrip("\ufeff")

    try:
        rows = list(csv.reader(io.StringIO(content)))
    except csv.Error as exc:
        raise ServiceError("IMPORT_INVALID", f"CSV 格式错误：{exc}")

    keys = []
    for line_no, row in enumerate(rows, start=1):
        if not row:
            continue
        value = row[0].strip()
        if line_no == 1 and value.lower() == "key":
            continue
        if not value:
            raise ServiceError("IMPORT_INVALID", f"第 {line_no} 行 key 为空")
        if len(value) > KEY_MAX_LENGTH:
            raise ServiceError("IMPORT_INVALID", f"第 {line_no} 行 key 超过 {KEY_MAX_LENGTH} 个字符")
        keys.append(value)

    if not keys:
        raise ServiceError("IMPORT_INVALID", "导入文件中没有 key")
    return keys


def import_keys(product, duration_days, content):
    _check_duration(duration_days)
    keys = parse_import_csv(content)

    seen, duplicates = set(), []
    for k in keys:
        if k in seen and k not in duplicates:
            duplicates.append(k)
        seen.add(k)

    with transaction.atomic():
        existing = LicenseKey.objects.filter(key__in=seen).values_list("key", flat=True)
        duplicates += sorted(k for k in existing if k not in duplicates)
        if duplicates:
            raise ServiceError(
                "IMPORT_DUPLICATE", f"有 {len(duplicates)} 个重复的 key，整批未导入", duplicates
            )
        return LicenseKey.objects.bulk_create(
            LicenseKey(key=k, product=product, duration_days=duration_days) for k in keys
        )


def _device_label(lic):
    activation = getattr(lic, "activation", None)
    if activation is None:
        return ""
    if activation.device_info:
        return f"{activation.device_info}（{activation.device_hash}）"
    return activation.device_hash


def export_keys_csv(product):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)
    queryset = (
        LicenseKey.objects.filter(product=product)
        .select_related("activation")
        .prefetch_related("transfer_logs")
        .order_by("created_at", "id")
    )
    for lic in queryset:
        expires = timezone.localtime(lic.expires_at).strftime("%Y-%m-%d %H:%M:%S") if lic.expires_at else ""
        writer.writerow(
            [lic.key, lic.status, lic.duration_days, expires, _device_label(lic), len(lic.transfer_logs.all())]
        )
    return buffer.getvalue()
