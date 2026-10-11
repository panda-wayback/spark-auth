from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Min
from django.utils import timezone

from keys import codes
from keys import services as keys_services
from keys.errors import ServiceError

from .models import Redemption

MAX_REDEEM_COUNT = 1000


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
    duration_days: int | None
    first_redeemed_at: datetime
    last_redeemed_at: datetime

    @property
    def remaining(self):
        return self.uses - self.used

    @property
    def expires_at(self):
        return _expires_at(self.first_redeemed_at, self.duration_days)

    @property
    def expired(self):
        return _is_expired(self.expires_at)

    @property
    def status(self):
        if self.remaining <= 0:
            return "已用完"
        if self.expired:
            return "已到期"
        return "使用中"


@dataclass(frozen=True)
class CodeStatus:
    uses: int
    used: int
    expires_at: datetime | None

    @property
    def remaining(self):
        return self.uses - self.used

    @property
    def expired(self):
        return _is_expired(self.expires_at)


@dataclass(frozen=True)
class RedeemResult:
    remaining: int
    redeemed_at: datetime
    expires_at: datetime | None
    code: str
    product_code: str
    seq: int
    uses: int
    ip: str | None


def _expires_at(first_redeemed_at, duration_days):
    if first_redeemed_at is None or duration_days is None:
        return None
    return first_redeemed_at + timedelta(days=duration_days)


def _is_expired(expires_at, now=None):
    if expires_at is None:
        return False
    return (now or timezone.now()) > expires_at


def _usages(redemptions):
    rows = (
        redemptions.order_by()
        .values("code", "product_code", "uses")
        .annotate(
            used=Count("id"),
            first=Min("redeemed_at"),
            last=Max("redeemed_at"),
            duration_days=Max("duration_days"),
        )
        .order_by("-last", "code")
    )
    return [
        CodeUsage(
            r["code"],
            r["product_code"],
            r["used"],
            r["uses"],
            r["duration_days"],
            r["first"],
            r["last"],
        )
        for r in rows
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


def _parse_count(count):
    if count is None:
        return 1
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > MAX_REDEEM_COUNT:
        raise ServiceError("REQUEST_INVALID", f"count 须为 1 至 {MAX_REDEEM_COUNT} 的整数")
    return count


def _checked_count_batch(code):
    code = codes.normalize(code)
    batch = keys_services.check_code(code)
    product = keys_services.get_product(batch.product_id)
    if product.disabled:
        raise ServiceError("PRODUCT_DISABLED", "软件已禁用")
    if product.kind != "count":
        raise ServiceError("CODE_TYPE_MISMATCH", "设备激活卡密不能核销")
    return code, batch, product


def status(code):
    code, batch, _product = _checked_count_batch(code)
    first = Redemption.objects.filter(code=code).order_by("seq", "id").first()
    used = Redemption.objects.filter(code=code).count()
    if first is None:
        expires_at = None
    else:
        expires_at = _expires_at(first.redeemed_at, first.duration_days)
    return CodeStatus(uses=batch.uses, used=used, expires_at=expires_at)


def redeem(code, *, count=None, ip=None):
    count = _parse_count(count)
    code, batch, product = _checked_count_batch(code)
    while True:
        first = Redemption.objects.filter(code=code).order_by("seq", "id").first()
        used = Redemption.objects.filter(code=code).count()
        if first is not None:
            expires = _expires_at(first.redeemed_at, first.duration_days)
            if _is_expired(expires):
                raise ServiceError("CODE_EXPIRED", "卡密已到期")
        remaining = batch.uses - used
        if remaining <= 0:
            raise ServiceError("CODE_USED", "卡密次数已用完")
        over = remaining < count
        duration_days = first.duration_days if first is not None else batch.duration_days
        try:
            with transaction.atomic():
                now = timezone.now()
                last = None
                for i in range(count):
                    last = Redemption.objects.create(
                        code=code,
                        product_code=product.code,
                        seq=used + 1 + i,
                        uses=batch.uses,
                        duration_days=duration_days,
                        redeemed_at=now,
                        ip=ip,
                    )
        except IntegrityError:
            continue
        if over:
            raise ServiceError("CODE_USED", "扣减超过剩余次数，卡密已作废")
        first_at = first.redeemed_at if first is not None else last.redeemed_at
        return RedeemResult(
            remaining=batch.uses - (used + count),
            redeemed_at=last.redeemed_at,
            expires_at=_expires_at(first_at, duration_days),
            code=last.code,
            product_code=last.product_code,
            seq=last.seq,
            uses=last.uses,
            ip=last.ip,
        )


def count_by_product():
    rows = Redemption.objects.order_by().values("product_code").annotate(n=Count("id"))
    return {row["product_code"]: row["n"] for row in rows}


def count_in_use_by_product():
    rows = (
        Redemption.objects.order_by()
        .values("product_code", "code", "uses")
        .annotate(used=Count("id"), first=Min("redeemed_at"), duration_days=Max("duration_days"))
        .filter(used__lt=F("uses"))
    )
    counts = {}
    now = timezone.now()
    for row in rows:
        if _is_expired(_expires_at(row["first"], row["duration_days"]), now):
            continue
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
