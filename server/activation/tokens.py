from django.core import signing

from keys.errors import ServiceError

_SALT = "spark-auth.activation-token"


def issue(product_code, key, device_hash):
    return signing.dumps({"p": product_code, "k": key, "d": device_hash}, salt=_SALT)


def read(token):
    try:
        payload = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        raise ServiceError("TOKEN_INVALID", "token 无效")
    if not isinstance(payload, dict) or not all(isinstance(payload.get(f), str) for f in ("p", "k", "d")):
        raise ServiceError("TOKEN_INVALID", "token 无效")
    return payload["p"], payload["k"], payload["d"]
