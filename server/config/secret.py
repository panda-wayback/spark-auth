import os
import secrets
from pathlib import Path

FILE_NAME = "secret_key"


def load_secret_key(db_path):
    value = os.environ.get("SPARK_AUTH_SECRET_KEY", "").strip()
    if value:
        return value
    path = Path(db_path).parent / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text().strip()
        if not value:
            raise RuntimeError(f"服务端密钥文件为空：{path}")
        return value
    value = secrets.token_urlsafe(50)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    return value
