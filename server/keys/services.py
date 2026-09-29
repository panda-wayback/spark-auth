import csv
import io

from django.utils import timezone

from . import codes
from .errors import ServiceError
from .models import SigningKey

BATCH_MAX = 1000
DURATION_MAX = 36500
EXPORT_HEADER = ["卡密", "有效时长（天）", "签发时间"]


def issue_codes(product, duration_days, count=1):
    if not isinstance(count, int) or not 1 <= count <= BATCH_MAX:
        raise ServiceError("BATCH_COUNT_INVALID", f"生成数量必须在 1 到 {BATCH_MAX} 之间")
    if not isinstance(duration_days, int) or not 1 <= duration_days <= DURATION_MAX:
        raise ServiceError("REQUEST_INVALID", f"有效时长必须是 1 到 {DURATION_MAX} 之间的整数（天）")

    signing_key = SigningKey.objects.create(
        key_id=codes.new_key_id(),
        product=product,
        private_pem=codes.new_private_pem(),
        duration_days=duration_days,
        count=count,
        created_at=timezone.now().replace(microsecond=0),
    )
    return signing_key, batch_codes(signing_key)


def batch_codes(signing_key):
    return codes.sign_codes(
        signing_key.private_pem,
        signing_key.key_id,
        signing_key.product.code,
        int(signing_key.created_at.timestamp()),
        signing_key.duration_days,
        range(1, signing_key.count + 1),
    )


def export_codes_csv(signing_keys):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)
    for signing_key in signing_keys:
        issued = timezone.localtime(signing_key.created_at).strftime("%Y-%m-%d %H:%M:%S")
        for code in batch_codes(signing_key):
            writer.writerow([code, signing_key.duration_days, issued])
    return buffer.getvalue()
