# Spark Auth

个人用的软件激活服务端：按软件签发自包含卡密，用户在软件里输入卡密后绑定到一台设备。

- 卡密自带软件标识、有效时长和签名，服务端只保存已激活的记录。
- 每次签发生成一个新的私钥批次；禁用批次只阻止新激活，已激活设备不受影响。
- 一个卡密同一时刻只绑定一台设备，可按软件设置是否允许换设备、换一次扣多少时长。
- 极简网页后台管理一切；客户端不在本仓库，接入方调两个 HTTP 接口即可。

技术栈：Python + Django + SQLite。

## 快速开始

```bash
make install     # 创建 .venv 并安装依赖
make migrate     # 创建数据库
make superuser   # 创建管理员账号
make run         # 启动开发服务（8000 端口）
```

浏览器打开 <http://127.0.0.1:8000/>，登录后：新建软件 → 管理卡密 → 签发卡密。

其它命令：`make test` 运行测试，`make tester` 启动激活测试页（<http://127.0.0.1:8002/>，粘贴卡密即可测试激活与校验）。

## 访问地址

只有允许列表里的域名或 IP 能访问服务（含后台、客户端接口、MCP）。

- `localhost`、`127.0.0.1` 始终允许。
- 其它地址在后台「访问地址」页添加：局域网 IP、公网 IP 可一键添加，域名手动填写。
- 列表保存在 `server/allowed_hosts.txt`（一行一个），随代码提交。

## 客户端接入

- 激活：`POST /api/activate`，校验：`POST /api/verify`，详见 [docs/client/activation](docs/client/activation/README.md)。
- 让 AI 写接入代码：在后台「MCP 接入」页复制配置到接入方项目的 `.cursor/mcp.json`，AI 即可拿到服务地址、接口与接入规范。

## 部署

```bash
export SPARK_AUTH_SECRET_KEY='一串足够长的随机字符'   # 必填，token 签名依赖它，更换后已签发 token 全部失效
export SPARK_AUTH_DB_PATH=/path/to/db.sqlite3        # 可选，默认 server/db.sqlite3
cd server && ../.venv/bin/python manage.py migrate
../.venv/bin/gunicorn config.wsgi -b 127.0.0.1:8000 --workers 2
```

- gunicorn 只监听本机，由 nginx / caddy 对外并配置域名。
- nginx 需转发 `Host` 与协议头；caddy 默认已转发：

  ```nginx
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
  ```

- 部署时带上 `docs/` 目录（MCP 接入说明从中读取）和 `server/allowed_hosts.txt`。
- 首次用域名访问前，先把域名加入 `server/allowed_hosts.txt`（本机后台添加后提交即可）。
- 开发模式需设置 `SPARK_AUTH_DEBUG=1`（`make` 命令已自动设置），生产环境不要设置。

## 目录

```text
docs/              能力文档（服务端 / 客户端规范）
server/keys/       数据表、卡密签发与导出
server/activation/ 激活、校验接口与 MCP
server/console/    管理后台与访问地址校验
tools/             激活测试页
```
