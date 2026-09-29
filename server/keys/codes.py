import base64
import binascii
import secrets
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import ServiceError

_HEADER = struct.Struct(">8sIII")
_SIGNATURE_LENGTH = 64


@dataclass(frozen=True)
class CodeInfo:
    key_id: str
    product_code: str
    issued_at: int
    duration_days: int
    serial: int


def new_key_id():
    return secrets.token_hex(8)


def new_private_pem():
    return (
        Ed25519PrivateKey.generate()
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("ascii")
    )


def _load_private(private_pem):
    return serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)


def normalize(code):
    return "".join(code.split()).replace("-", "").upper()


def sign_codes(private_pem, key_id, product_code, issued_at, duration_days, serials):
    private_key = _load_private(private_pem)
    key_id_bytes = bytes.fromhex(key_id)
    product_bytes = product_code.encode("utf-8")
    codes = []
    for serial in serials:
        payload = _HEADER.pack(key_id_bytes, issued_at, duration_days, serial) + product_bytes
        raw = payload + private_key.sign(payload)
        codes.append(base64.b32encode(raw).decode("ascii").rstrip("="))
    return codes


def _decode(code):
    code = normalize(code)
    try:
        raw = base64.b32decode(code + "=" * (-len(code) % 8))
    except (binascii.Error, ValueError):
        raise ServiceError("CODE_INVALID", "卡密格式错误")
    if len(raw) <= _HEADER.size + _SIGNATURE_LENGTH:
        raise ServiceError("CODE_INVALID", "卡密格式错误")
    return raw[:-_SIGNATURE_LENGTH], raw[-_SIGNATURE_LENGTH:]


def read_key_id(code):
    payload, _ = _decode(code)
    return payload[:8].hex()


def verify_code(private_pem, code):
    payload, signature = _decode(code)
    try:
        _load_private(private_pem).public_key().verify(signature, payload)
    except InvalidSignature:
        raise ServiceError("CODE_INVALID", "卡密签名无效")
    key_id, issued_at, duration_days, serial = _HEADER.unpack(payload[: _HEADER.size])
    try:
        product_code = payload[_HEADER.size :].decode("utf-8")
    except UnicodeDecodeError:
        raise ServiceError("CODE_INVALID", "卡密格式错误")
    return CodeInfo(key_id.hex(), product_code, issued_at, duration_days, serial)
