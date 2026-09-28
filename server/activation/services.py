from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from keys.errors import ServiceError
from keys.models import Activation, LicenseKey, Product, TransferLog

from . import tokens


def _load_checked(product_code, key, for_update=False):
    product = Product.objects.filter(code=product_code).first()
    if product is None:
        raise ServiceError("PRODUCT_NOT_FOUND", "软件不存在")
    if product.disabled:
        raise ServiceError("PRODUCT_DISABLED", "软件已禁用")

    queryset = LicenseKey.objects.select_related("product")
    if for_update:
        queryset = queryset.select_for_update()
    lic = queryset.filter(key=key).first()
    if lic is None:
        raise ServiceError("KEY_NOT_FOUND", "key 不存在")
    if lic.product_id != product.id:
        raise ServiceError("KEY_PRODUCT_MISMATCH", "key 不属于该软件")
    if lic.disabled:
        raise ServiceError("KEY_DISABLED", "key 已禁用")
    if lic.is_expired():
        raise ServiceError("KEY_EXPIRED", "key 已到期")
    return product, lic


def activate(product_code, key, device_hash, device_info=""):
    now = timezone.now()
    with transaction.atomic():
        product, lic = _load_checked(product_code, key, for_update=True)
        current = Activation.objects.filter(key=lic).first()

        if current is None:
            lic.expires_at = now + timedelta(days=lic.duration_days)
            lic.save(update_fields=["expires_at"])
            Activation.objects.create(
                key=lic, device_hash=device_hash, device_info=device_info, activated_at=now
            )
        elif current.device_hash != device_hash:
            if not product.allow_transfer:
                raise ServiceError("TRANSFER_NOT_ALLOWED", "该软件不允许换设备")
            penalty = product.transfer_penalty_hours
            new_expires = lic.expires_at - timedelta(hours=penalty)
            if new_expires <= now:
                raise ServiceError("TRANSFER_INSUFFICIENT_TIME", "剩余时长不足以扣减，无法换设备")

            TransferLog.objects.create(
                key=lic,
                old_device_hash=current.device_hash,
                old_device_info=current.device_info,
                new_device_hash=device_hash,
                new_device_info=device_info,
                penalty_hours=penalty,
                expires_before=lic.expires_at,
                expires_after=new_expires,
                created_at=now,
            )
            current.device_hash = device_hash
            current.device_info = device_info
            current.activated_at = now
            current.save(update_fields=["device_hash", "device_info", "activated_at"])
            lic.expires_at = new_expires
            lic.save(update_fields=["expires_at"])

    return tokens.issue(product.code, lic.key, device_hash), lic.expires_at


def verify(product_code, device_hash, token):
    token_product, key, token_device = tokens.read(token)
    if token_product != product_code:
        raise ServiceError("TOKEN_INVALID", "token 不属于该软件")
    if token_device != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "token 与当前设备不一致")

    _, lic = _load_checked(product_code, key)
    current = Activation.objects.filter(key=lic).first()
    if current is None or current.device_hash != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "该 key 已绑定到其他设备")
    return lic.expires_at
