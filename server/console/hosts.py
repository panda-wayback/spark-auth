import ipaddress
import os
import re
import socket
import urllib.request
from pathlib import Path

from django.conf import settings
from django.http.request import split_domain_port, validate_host

from keys.errors import ServiceError

BUILTIN_HOSTS = ("localhost", "127.0.0.1")
PUBLIC_IP_URL = "https://api.ipify.org"
PUBLIC_IP_TIMEOUT = 5

_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^\.?{_LABEL}(\.{_LABEL})*$")


def builtin_hosts():
    # settings.ALLOWED_HOSTS 固定为 "*"，由本模块校验；其中的非通配项（如测试环境追加的 testserver）同样放行
    return [*BUILTIN_HOSTS, *(h for h in settings.ALLOWED_HOSTS if h != "*")]


def file_hosts():
    try:
        lines = Path(settings.ALLOWED_HOSTS_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#")]


def _write(hosts):
    path = Path(settings.ALLOWED_HOSTS_FILE)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{host}\n" for host in hosts), encoding="utf-8")
    os.replace(tmp, path)


def _patterns():
    return [*builtin_hosts(), *file_hosts()]


def request_domain(request):
    domain, _ = split_domain_port(request.get_host())
    return domain


def is_allowed(domain):
    return bool(domain) and validate_host(domain, _patterns())


def lan_ips():
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos if not info[4][0].startswith("127.")})


def candidates():
    patterns = _patterns()
    return [ip for ip in lan_ips() if not validate_host(ip, patterns)]


def public_ip():
    try:
        with urllib.request.urlopen(PUBLIC_IP_URL, timeout=PUBLIC_IP_TIMEOUT) as resp:
            value = resp.read(64).decode("ascii").strip()
        return str(ipaddress.IPv4Address(value))
    except (OSError, ValueError):
        raise ServiceError("PUBLIC_IP_UNAVAILABLE", "检测公网 IP 失败，请确认服务器能访问外网后重试")


def add_host(value):
    host = value.strip().lower()
    if len(host) > 253 or not _HOST.match(host):
        raise ServiceError("HOST_INVALID", "只填域名或 IP，不含协议、端口和路径，例如 auth.example.com 或 192.168.1.10")
    hosts = file_hosts()
    if host in builtin_hosts() or host in hosts:
        raise ServiceError("HOST_INVALID", f"{host} 已在允许列表中")
    _write([*hosts, host])
    return host


def remove_host(host, current_domain):
    hosts = file_hosts()
    if host not in hosts:
        raise ServiceError("HOST_INVALID", f"{host} 不在可删除的列表中")
    remaining = [h for h in hosts if h != host]
    if not validate_host(current_domain, [*builtin_hosts(), *remaining]):
        raise ServiceError("HOST_IN_USE", f"你正在通过 {current_domain} 访问，删除后会无法访问后台")
    _write(remaining)
