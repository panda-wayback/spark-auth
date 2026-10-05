import base64
import binascii
import secrets
import struct

from django.utils.crypto import constant_time_compare, salted_hmac

from .errors import ServiceError

_SALT = "spark-auth.code"
_BATCH_ID_BYTES = 5
_BATCH_SECRET_BYTES = 16
_SERIAL = struct.Struct(">H")
_MAC_BYTES = 10
_RAW_LENGTH = _BATCH_ID_BYTES + _SERIAL.size + _MAC_BYTES
_GROUP = 5


def new_batch_id():
    return secrets.token_hex(_BATCH_ID_BYTES)


def new_batch_secret():
    return secrets.token_hex(_BATCH_SECRET_BYTES)


def _mac(batch_secret, message):
    return salted_hmac(f"{_SALT}:{batch_secret}", message, algorithm="sha256").digest()[:_MAC_BYTES]


def normalize(code):
    return "".join(code.split()).replace("-", "").upper()


def _format(raw):
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i : i + _GROUP] for i in range(0, len(text), _GROUP))


def _decode(code):
    code = normalize(code)
    try:
        raw = base64.b32decode(code + "=" * (-len(code) % 8))
    except (binascii.Error, ValueError):
        raise ServiceError("CODE_INVALID", "卡密格式错误")
    if len(raw) != _RAW_LENGTH:
        raise ServiceError("CODE_INVALID", "卡密格式错误")
    return raw[:-_MAC_BYTES], raw[-_MAC_BYTES:]


def sign(batch_id, batch_secret, serial):
    message = bytes.fromhex(batch_id) + _SERIAL.pack(serial)
    return _format(message + _mac(batch_secret, message))


def read(code):
    message, _ = _decode(code)
    (serial,) = _SERIAL.unpack(message[_BATCH_ID_BYTES:])
    return message[:_BATCH_ID_BYTES].hex(), serial


def verify(code, batch_secret):
    message, mac = _decode(code)
    if not constant_time_compare(mac, _mac(batch_secret, message)):
        raise ServiceError("CODE_INVALID", "卡密无效")
