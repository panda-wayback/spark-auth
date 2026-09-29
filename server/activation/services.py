from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from keys import codes
from keys.errors import ServiceError
from keys.models import Activation, Product, SigningKey, TransferLog

from . import tokens


def _load_product(product_code):
    product = Product.objects.filter(code=product_code).first()
    if product is None:
        raise ServiceError("PRODUCT_NOT_FOUND", "软件不存在")
    if product.disabled:
        raise ServiceError("PRODUCT_DISABLED", "软件已禁用")
    return product


def _check_usable(activation, now):
    if activation.disabled:
        raise ServiceError("CODE_DISABLED", "卡密已禁用")
    if activation.is_expired(now):
        raise ServiceError("CODE_EXPIRED", "卡密已到期")


def _read_new_code(product, code):
    signing_key = SigningKey.objects.filter(key_id=codes.read_key_id(code)).first()
    if signing_key is None:
        raise ServiceError("SIGNING_KEY_NOT_FOUND", "卡密所属批次不存在")
    if signing_key.disabled:
        raise ServiceError("SIGNING_KEY_DISABLED", "卡密所属批次已禁用，不能激活")
    info = codes.verify_code(signing_key.private_pem, code)
    if signing_key.product_id != product.id or info.product_code != product.code:
        raise ServiceError("CODE_PRODUCT_MISMATCH", "卡密不属于该软件")
    return info


def activate(product_code, code, device_hash, device_info=""):
    code = codes.normalize(code)
    now = timezone.now()
    with transaction.atomic():
        product = _load_product(product_code)
        activation = Activation.objects.select_for_update().filter(code=code).first()

        if activation is None:
            info = _read_new_code(product, code)
            activation = Activation.objects.create(
                code=code,
                product=product,
                device_hash=device_hash,
                device_info=device_info,
                duration_days=info.duration_days,
                expires_at=now + timedelta(days=info.duration_days),
                activated_at=now,
            )
        else:
            if activation.product_id != product.id:
                raise ServiceError("CODE_PRODUCT_MISMATCH", "卡密不属于该软件")
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
                    penalty_hours=penalty,
                    expires_before=activation.expires_at,
                    expires_after=new_expires,
                    created_at=now,
                )
                activation.device_hash = device_hash
                activation.device_info = device_info
                activation.activated_at = now
                activation.expires_at = new_expires
                activation.save(update_fields=["device_hash", "device_info", "activated_at", "expires_at"])

    return tokens.issue(product.code, code, device_hash), activation.expires_at


def verify(product_code, device_hash, token):
    token_product, code, token_device = tokens.read(token)
    if token_product != product_code:
        raise ServiceError("TOKEN_INVALID", "token 不属于该软件")
    if token_device != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "token 与当前设备不一致")

    product = _load_product(product_code)
    activation = Activation.objects.filter(code=code, product=product).first()
    if activation is None:
        raise ServiceError("TOKEN_INVALID", "token 无效")
    _check_usable(activation, timezone.now())
    if activation.device_hash != device_hash:
        raise ServiceError("DEVICE_MISMATCH", "该卡密已绑定到其他设备")
    return activation.expires_at
