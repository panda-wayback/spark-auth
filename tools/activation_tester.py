"""激活码测试服务：提供测试页面，并把 /api/activate、/api/verify 转发到主服务。

用法：python tools/activation_tester.py [--port 8002] [--upstream http://127.0.0.1:8000]
"""

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>激活码测试</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; background: #f5f6f8; margin: 0; color: #222; }
  main { max-width: 760px; margin: 32px auto; padding: 0 16px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 20px; }
  .card { background: #fff; border: 1px solid #e3e5e8; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  label { display: block; font-size: 14px; margin: 12px 0 4px; }
  label:first-child { margin-top: 0; }
  input, textarea { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #ccd0d5;
    border-radius: 6px; font-size: 14px; font-family: inherit; }
  textarea { font-family: ui-monospace, Menlo, monospace; font-size: 13px; resize: vertical; word-break: break-all; }
  .hint { color: #888; font-size: 12px; margin-top: 4px; word-break: break-all; }
  .row { display: flex; gap: 8px; margin-top: 16px; }
  button { padding: 8px 18px; border: 0; border-radius: 6px; background: #1f6feb; color: #fff; font-size: 14px; cursor: pointer; }
  button.secondary { background: #e9ecef; color: #222; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 13px; }
  .ok { color: #1a7f37; } .err { color: #cf222e; }
</style>
</head>
<body>
<main>
  <h1>激活码测试</h1>
  <div class="sub">请求转发到主服务：<code>__UPSTREAM__</code></div>

  <div class="card">
    <label for="code">卡密</label>
    <textarea id="code" rows="4" placeholder="粘贴卡密"></textarea>
    <label for="product">软件标识 product</label>
    <input id="product" placeholder="粘贴卡密后自动识别">
    <div class="hint">从卡密中读取，可手动改（用于测试软件不匹配）</div>
    <label for="device">设备名</label>
    <input id="device" value="测试设备-1">
    <div class="hint">device_hash = sha256(设备名)，改设备名即模拟换设备：<span id="hash"></span></div>
    <label for="token">token</label>
    <textarea id="token" rows="3" placeholder="激活成功后自动填入"></textarea>
    <div class="row">
      <button id="activate">激活</button>
      <button id="verify" class="secondary">校验</button>
    </div>
  </div>

  <div class="card">
    <div id="status">尚未请求</div>
    <pre id="output"></pre>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);

async function deviceHash() {
  const data = new TextEncoder().encode($("device").value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function refreshHash() { $("hash").textContent = await deviceHash(); }

async function call(path, body) {
  $("status").textContent = "请求中…";
  $("status").className = "";
  $("output").textContent = "";
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    let data;
    try { data = JSON.parse(text); } catch { data = null; }
    const ok = data && data.ok;
    $("status").textContent = `${path} → HTTP ${resp.status}` + (data && data.error ? `  ${data.error.code}：${data.error.message}` : "");
    $("status").className = ok ? "ok" : "err";
    $("output").textContent = data ? JSON.stringify(data, null, 2) : text;
    return data;
  } catch (e) {
    $("status").textContent = "请求失败：" + e;
    $("status").className = "err";
  }
}

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const HEADER_LENGTH = 20;
const SIGNATURE_LENGTH = 64;

function productFromCode(code) {
  const text = code.replace(/[\\s-]/g, "").toUpperCase();
  const bytes = [];
  let bits = 0, value = 0;
  for (const ch of text) {
    const index = BASE32.indexOf(ch);
    if (index < 0) return "";
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((value >> bits) & 0xff);
    }
  }
  if (bytes.length <= HEADER_LENGTH + SIGNATURE_LENGTH) return "";
  try {
    const product = bytes.slice(HEADER_LENGTH, bytes.length - SIGNATURE_LENGTH);
    return new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(product));
  } catch {
    return "";
  }
}

$("device").addEventListener("input", refreshHash);
$("code").addEventListener("input", () => { $("product").value = productFromCode($("code").value); });

$("activate").addEventListener("click", async () => {
  const data = await call("/api/activate", {
    product: $("product").value,
    code: $("code").value,
    device_hash: await deviceHash(),
    device_info: $("device").value,
  });
  if (data && data.ok) $("token").value = data.token;
});

$("verify").addEventListener("click", async () => {
  await call("/api/verify", {
    product: $("product").value,
    device_hash: await deviceHash(),
    token: $("token").value.trim(),
  });
});

refreshHash();
</script>
</body>
</html>
"""

PROXY_PATHS = {"/api/activate", "/api/verify"}


def make_handler(upstream):
    page = PAGE.replace("__UPSTREAM__", upstream).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/":
                self._send(404, "text/plain; charset=utf-8", "Not Found".encode("utf-8"))
                return
            self._send(200, "text/html; charset=utf-8", page)

        def do_POST(self):
            if self.path not in PROXY_PATHS:
                self._send(404, "text/plain; charset=utf-8", "Not Found".encode("utf-8"))
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            request = urllib.request.Request(
                upstream + self.path,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as resp:
                    status, content = resp.status, resp.read()
                    content_type = resp.headers.get("Content-Type", "application/json")
            except urllib.error.HTTPError as exc:
                status, content = exc.code, exc.read()
                content_type = exc.headers.get("Content-Type", "application/json")
            except urllib.error.URLError as exc:
                message = {"ok": False, "error": {"code": "UPSTREAM_UNREACHABLE", "message": f"无法连接主服务：{exc.reason}"}}
                status, content = 502, json.dumps(message, ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
            self._send(status, content_type, content)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="激活码测试服务")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    upstream = args.upstream.rstrip("/")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(upstream))
    print(f"激活码测试页：http://127.0.0.1:{args.port}/  →  转发到 {upstream}")
    server.serve_forever()


if __name__ == "__main__":
    main()
