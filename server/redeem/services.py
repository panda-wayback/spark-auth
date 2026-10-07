from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Min
from django.utils import timezone

from keys import codes
from keys import services as keys_services
from keys.errors import ServiceError

from .models import Redemption


@dataclass(frozen=True)
class RedemptionInfo:
    code: str
    product_code: str
    seq: int
    uses: int
    redeemed_at: datetime
    ip: str | None

    @property
    def remaining(self):
        return self.uses - self.seq


@dataclass(frozen=True)
class CodeUsage:
    code: str
    product_code: str
    used: int
    uses: int
    first_redeemed_at: datetime
    last_redeemed_at: datetime

    @property
    def remaining(self):
        return self.uses - self.used

    @property
    def status(self):
        return "使用中" if self.remaining > 0 else "已用完"


def _usages(redemptions):
    rows = (
        redemptions.order_by()
        .values("code", "product_code", "uses")
        .annotate(used=Count("id"), first=Min("redeemed_at"), last=Max("redeemed_at"))
        .order_by("-last", "code")
    )
    return [
        CodeUsage(r["code"], r["product_code"], r["used"], r["uses"], r["first"], r["last"]) for r in rows
    ]


def _info(redemption):
    return RedemptionInfo(
        redemption.code,
        redemption.product_code,
        redemption.seq,
        redemption.uses,
        redemption.redeemed_at,
        redemption.ip,
    )


def redeem(code, ip=None):
    code = codes.normalize(code)
    batch = keys_services.check_code(code)
    product = keys_services.get_product(batch.product_id)
    if product.disabled:
        raise ServiceError("PRODUCT_DISABLED", "软件已禁用")
    if product.kind != "count":
        raise ServiceError("CODE_TYPE_MISMATCH", "设备激活卡密不能核销")
    while True:
        used = Redemption.objects.filter(code=code).count()
        if used >= batch.uses:
            raise ServiceError("CODE_USED", "卡密次数已用完")
        try:
            with transaction.atomic():
                redemption = Redemption.objects.create(
                    code=code,
                    product_code=product.code,
                    seq=used + 1,
                    uses=batch.uses,
                    redeemed_at=timezone.now(),
                    ip=ip,
                )
        except IntegrityError:
            continue
        return _info(redemption)


def count_by_product():
    rows = Redemption.objects.order_by().values("product_code").annotate(n=Count("id"))
    return {row["product_code"]: row["n"] for row in rows}


def count_in_use_by_product():
    rows = (
        Redemption.objects.order_by()
        .values("product_code", "code", "uses")
        .annotate(used=Count("id"))
        .filter(used__lt=F("uses"))
    )
    counts = {}
    for row in rows:
        counts[row["product_code"]] = counts.get(row["product_code"], 0) + 1
    return counts


def delete_by_product(product_code):
    deleted, _ = Redemption.objects.filter(product_code=product_code).delete()
    return deleted


def search(product_code, query=""):
    redemptions = Redemption.objects.filter(product_code=product_code)
    query = query.strip()
    if query:
        redemptions = redemptions.filter(code__icontains=codes.normalize(query))
    return _usages(redemptions)


def get_code_detail(code):
    redemptions = Redemption.objects.filter(code=codes.normalize(code))
    usages = _usages(redemptions)
    if not usages:
        raise ServiceError("REDEMPTION_NOT_FOUND", "该卡密没有核销记录")
    return usages[0], [_info(r) for r in redemptions.order_by("-seq")]
