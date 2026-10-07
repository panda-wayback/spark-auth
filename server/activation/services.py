from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from keys import codes
from keys import services as keys_services
from keys.errors import ServiceError

from . import tokens
from .models import Activation, TransferLog


@dataclass(frozen=True)
class ActivationInfo:
    id: int
    code: str
    product_code: str
    device_hash: str
    device_info: str
    ip: str | None
    duration_days: int
    expires_at: datetime
    disabled: bool
    activated_at: datetime
    transfer_count: int
    status: str


@dataclass(frozen=True)
class TransferInfo:
    old_device_hash: str
    old_device_info: str
    new_device_hash: str
    new_device_info: str
    new_ip: str | None
    penalty_hours: int
    expires_before: datetime
    expires_after: datetime
    created_at: datetime


def _is_expired(activation, now):
    return activation.expires_at <= now


def _status(activation, now):
    if activation.disabled:
        return "已禁用"
    if _is_expired(activation, now):
        return "已到期"
    return "有效"


def _activation_info(activation, transfer_count, now):
    return ActivationInfo(
        activation.id,
        activation.code,
        activation.product_code,
        activation.device_hash,
        activation.device_info,
        activation.ip,
        activation.duration_days,
        activation.expires_at,
        activation.disabled,
        activation.activated_at,
        transfer_count,
        _status(activation, now),
    )


def _transfer_info(log):
    return TransferInfo(
        log.old_device_hash,
        log.old_device_info,
        log.new_device_hash,
        log.new_device_info,
        log.new_ip,
        log.penalty_hours,
        log.expires_before,
        log.expires_after,
        log.created_at,
    )


def _load_product(product_code):
    product = keys_services.get_product_by_code(product_code)
    if product.disabled:
        raise ServiceError("PRODUCT_DISABLED", "软件已禁用")
    return product


def _check_usable(activation, now):
    if activation.disabled:
        raise ServiceError("CODE_DISABLED", "卡密已禁用")
    if _is_expired(activation, now):
        raise ServiceError("CODE_EXPIRED", "卡密已到期")


def activate(code, device_hash, device_info="", ip=None):
    code = codes.normalize(code)
    now = timezone.now()
    with transaction.atomic():
        activation = Activation.objects.select_for_update().filter(code=code).first()

        if activation is None:
            batch = keys_services.check_code(code)
            product = _load_product(batch.product_code)
            if product.kind != "device":
                raise ServiceError("CODE_TYPE_MISMATCH", "按次数卡密不能用于设备激活")
            activation = Activation.objects.create(
                code=code,
                product_code=product.code,
                device_hash=device_hash,
                device_info=device_info,
                ip=ip,
                duration_days=batch.duration_days,
                expires_at=now + timedelta(days=batch.duration_days),
                activated_at=now,
            )
        else:
            product = _load_product(activation.product_code)
            _check_usable(activation, now)
            if activation.device_hash != device_hash:
                if not product.allow_transfer:
                    raise ServiceError("TRANSFER_NOT_ALLOWED", "该软件不允许换设备")
                penalty = product.transfer_penalty_hours
                new_expires = activation.expires_at - timedelta(hours=penalty)
                if new_expires <= now:
                    raise ServiceError("TRANSFER_INSUFFICIENT_TIME", "剩余时长不足以扣减，无法换设备")

                TransferLog.objects.create(
                    activation=activation,
                    old_device_hash=activation.device_hash,
                    old_device_info=activation.device_info,
                    new_device_hash=device_hash,
                    new_device_info=device_info,
                    new_ip=ip,
                    penalty_hours=penalty,
                    expires_before=activation.expires_at,
                    expires_after=new_expires,
                    created_at=now,
                )
                activation.device_hash = device_hash
                activation.device_info = device_info
                activation.ip = ip
                activation.activated_at = now
                activation.expires_at = new_expires
                activation.save(update_fields=["device_hash", "device_info", "ip", "activated_at", "expires_at"])

    return tokens.issue(product.code, code, device_hash), activation.expires_at


def verify(device_hash, token):
    product_code, code, token_device = tokens.read(token)
    if token_device != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "token 与当前设备不一致")

    product = _load_product(product_code)
    activation = Activation.objects.filter(code=code, product_code=product.code).first()
    if activation is None:
        raise ServiceError("TOKEN_INVALID", "token 无效")
    _check_usable(activation, timezone.now())
    if activation.device_hash != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "该卡密已绑定到其他设备")
    return activation.expires_at


def count_by_product():
    rows = Activation.objects.order_by().values("product_code").annotate(n=Count("id"))
    return {row["product_code"]: row["n"] for row in rows}


def count_in_use_by_product():
    rows = (
        Activation.objects.filter(disabled=False, expires_at__gt=timezone.now())
        .order_by()
        .values("product_code")
        .annotate(n=Count("id"))
    )
    return {row["product_code"]: row["n"] for row in rows}


def delete_by_product(product_code):
    with transaction.atomic():
        TransferLog.objects.filter(activation__product_code=product_code).delete()
        deleted, _ = Activation.objects.filter(product_code=product_code).delete()
    return deleted


def search(product_code, query=""):
    activations = Activation.objects.filter(product_code=product_code).annotate(n=Count("transfer_logs"))
    query = query.strip()
    if query:
        activations = activations.filter(
            Q(code__icontains=codes.normalize(query))
            | Q(device_info__icontains=query)
            | Q(device_hash__icontains=query)
        )
    now = timezone.now()
    return [_activation_info(a, a.n, now) for a in activations]


def _get(activation_id):
    activation = Activation.objects.filter(pk=activation_id).first()
    if activation is None:
        raise ServiceError("ACTIVATION_NOT_FOUND", "激活记录不存在")
    return activation


def get_detail(activation_id):
    activation = _get(activation_id)
    logs = [_transfer_info(log) for log in activation.transfer_logs.all()]
    return _activation_info(activation, len(logs), timezone.now()), logs


def toggle_disabled(activation_id):
    activation = _get(activation_id)
    activation.disabled = not activation.disabled
    activation.save(update_fields=["disabled"])
    return _activation_info(activation, activation.transfer_logs.count(), timezone.now())
