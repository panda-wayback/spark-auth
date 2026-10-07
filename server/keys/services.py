import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import codes
from .errors import ServiceError
from .models import Batch, Product

BATCH_MAX = 1000
DURATION_MAX = 36500
USES_MAX = 10000
PRODUCT_CODE_MAX = 64
PRODUCT_NAME_MAX = 128
PRODUCT_KINDS = ("device", "count")
EXPORT_HEADER = ["卡密", "有效时长（天）", "可用次数", "签发时间"]

_PRODUCT_CODE = re.compile(r"^[-a-zA-Z0-9_]+$")


@dataclass(frozen=True)
class ProductInfo:
    id: int
    code: str
    name: str
    kind: str
    allow_transfer: bool
    transfer_penalty_hours: int
    disabled: bool
    created_at: datetime

    @property
    def transfer_policy_label(self):
        if not self.allow_transfer:
            return "不允许换设备"
        if self.transfer_penalty_hours == 0:
            return "允许换设备，不扣时长"
        return f"允许换设备，每次扣 {self.transfer_penalty_hours} 小时"


@dataclass(frozen=True)
class BatchInfo:
    batch_id: str
    product_id: int
    product_code: str
    duration_days: int | None
    uses: int | None
    count: int
    disabled: bool
    created_at: datetime


def _product_info(product):
    return ProductInfo(
        product.id,
        product.code,
        product.name,
        product.kind,
        product.allow_transfer,
        product.transfer_penalty_hours,
        product.disabled,
        product.created_at,
    )


def _batch_info(batch):
    return BatchInfo(
        batch.batch_id,
        batch.product_id,
        batch.product.code,
        batch.duration_days,
        batch.uses,
        batch.count,
        batch.disabled,
        batch.created_at,
    )


def _product(product_id):
    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        raise ServiceError("PRODUCT_NOT_FOUND", "软件不存在")
    return product


def _check_policy(name, allow_transfer, transfer_penalty_hours):
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > PRODUCT_NAME_MAX:
        raise ServiceError("REQUEST_INVALID", f"名称不能为空且不超过 {PRODUCT_NAME_MAX} 个字符")
    if not isinstance(allow_transfer, bool):
        raise ServiceError("REQUEST_INVALID", "是否允许换设备必须是布尔值")
    if not isinstance(transfer_penalty_hours, int) or transfer_penalty_hours < 0:
        raise ServiceError("REQUEST_INVALID", "换设备扣减时长必须是不小于 0 的整数（小时）")
    return name


def list_products():
    return [_product_info(p) for p in Product.objects.all()]


def get_product(product_id):
    return _product_info(_product(product_id))


def get_product_by_code(code):
    product = Product.objects.filter(code=code).first()
    if product is None:
        raise ServiceError("PRODUCT_NOT_FOUND", "软件不存在")
    return _product_info(product)


def create_product(code, name, allow_transfer=False, transfer_penalty_hours=0, kind="device"):
    if not isinstance(code, str) or not _PRODUCT_CODE.match(code) or len(code) > PRODUCT_CODE_MAX:
        raise ServiceError("REQUEST_INVALID", f"标识只能用字母、数字、- 和 _，且不超过 {PRODUCT_CODE_MAX} 个字符")
    if kind not in PRODUCT_KINDS:
        raise ServiceError("REQUEST_INVALID", "类型只能是设备激活或按次数")
    name = _check_policy(name, allow_transfer, transfer_penalty_hours)
    try:
        with transaction.atomic():
            product = Product.objects.create(
                code=code,
                name=name,
                kind=kind,
                allow_transfer=allow_transfer,
                transfer_penalty_hours=transfer_penalty_hours,
            )
    except IntegrityError:
        raise ServiceError("PRODUCT_EXISTS", "该标识已被其它软件使用")
    return _product_info(product)


def update_product(product_id, name, allow_transfer, transfer_penalty_hours, disabled):
    product = _product(product_id)
    product.name = _check_policy(name, allow_transfer, transfer_penalty_hours)
    if not isinstance(disabled, bool):
        raise ServiceError("REQUEST_INVALID", "禁用状态必须是布尔值")
    product.allow_transfer = allow_transfer
    product.transfer_penalty_hours = transfer_penalty_hours
    product.disabled = disabled
    product.save(update_fields=["name", "allow_transfer", "transfer_penalty_hours", "disabled"])
    return _product_info(product)


def delete_product(product_id):
    product = _product(product_id)
    info = _product_info(product)
    with transaction.atomic():
        product.batches.all().delete()
        product.delete()
    return info


def _new_batch_id():
    while True:
        batch_id = codes.new_batch_id()
        if not Batch.objects.filter(batch_id=batch_id).exists():
            return batch_id


def _batch_codes(batch):
    return [codes.sign(batch.batch_id, batch.secret, serial) for serial in range(1, batch.count + 1)]


def issue_codes(product_id, duration_days, count=1, uses=None):
    product = _product(product_id)
    if not isinstance(count, int) or not 1 <= count <= BATCH_MAX:
        raise ServiceError("BATCH_COUNT_INVALID", f"生成数量必须在 1 到 {BATCH_MAX} 之间")
    if product.kind == "count":
        if duration_days is not None:
            raise ServiceError("REQUEST_INVALID", "按次数软件的卡密没有有效时长")
        if not isinstance(uses, int) or not 1 <= uses <= USES_MAX:
            raise ServiceError("REQUEST_INVALID", f"可用次数必须是 1 到 {USES_MAX} 之间的整数")
    else:
        if not isinstance(duration_days, int) or not 1 <= duration_days <= DURATION_MAX:
            raise ServiceError("REQUEST_INVALID", f"有效时长必须是 1 到 {DURATION_MAX} 之间的整数（天）")
        if uses is not None:
            raise ServiceError("REQUEST_INVALID", "设备激活软件的卡密没有可用次数")
    batch = Batch.objects.create(
        batch_id=_new_batch_id(),
        secret=codes.new_batch_secret(),
        product=product,
        duration_days=duration_days,
        uses=uses,
        count=count,
        created_at=timezone.now().replace(microsecond=0),
    )
    return _batch_info(batch), _batch_codes(batch)


def list_batches(product_id):
    return [_batch_info(b) for b in _product(product_id).batches.select_related("product")]


def disable_batch(batch_id):
    batch = Batch.objects.select_related("product").filter(batch_id=batch_id).first()
    if batch is None:
        raise ServiceError("BATCH_NOT_FOUND", "批次不存在")
    if not batch.disabled:
        batch.disabled = True
        batch.save(update_fields=["disabled"])
    return _batch_info(batch)


def export_codes_csv(product_id, batch_id=None):
    batches = _product(product_id).batches.order_by("created_at", "id")
    if batch_id is not None:
        batches = batches.filter(batch_id=batch_id)
        if not batches:
            raise ServiceError("BATCH_NOT_FOUND", "批次不存在")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)
    for batch in batches:
        issued = timezone.localtime(batch.created_at).strftime("%Y-%m-%d %H:%M:%S")
        for code in _batch_codes(batch):
            writer.writerow([code, batch.duration_days or "", batch.uses or "", issued])
    return buffer.getvalue()


def check_code(code):
    batch_id, serial = codes.read(code)
    batch = Batch.objects.select_related("product").filter(batch_id=batch_id).first()
    if batch is None or not 1 <= serial <= batch.count:
        raise ServiceError("CODE_INVALID", "卡密无效")
    codes.verify(code, batch.secret)
    if batch.disabled:
        raise ServiceError("BATCH_DISABLED", "卡密所属批次已禁用，不能激活")
    return _batch_info(batch)
