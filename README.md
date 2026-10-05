# Spark Auth

个人用的软件激活服务端：按软件签发自包含卡密，用户在软件里输入卡密后绑定到一台设备。

- 卡密约 28 个字符、便于手输，难以猜测、无法伪造；服务端只保存已激活的记录。
- 每次签发生成一个新的卡密批次；禁用批次只阻止新激活，已激活设备不受影响。
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
- 列表保存在 `server/allowed_hosts.txt`（一行一个），随代码提交；设置 `SPARK_AUTH_HOSTS_PATH` 时改存该路径。

## 客户端接入

- 激活：`POST /api/activate`，校验：`POST /api/verify`，详见 [docs/client/activation](docs/client/activation/README.md)。
- 让 AI 写接入代码：在后台「MCP 接入」页复制配置到接入方项目的 `.cursor/mcp.json`，AI 即可拿到服务地址、接口与接入规范。

## 部署

```bash
export SPARK_AUTH_SECRET_KEY='一串足够长的随机字符'   # 必填，卡密与 token 签名依赖它，更换后未激活卡密与已签发 token 全部失效
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

## Docker 部署

```bash
make install            # 本机虚拟环境，用于创建管理员账号；首次运行任一 make 命令会自动生成 .env
make superuser          # 创建管理员账号（与 Docker 共用数据库）
make docker-up          # 构建并启动，启动时自动迁移数据库
```

其它命令：`make docker-logs` 查看日志，`make docker-down` 停止。

- Docker 与本机共用 `server/db.sqlite3` 和 `server/allowed_hosts.txt`（挂载宿主机的 `server/` 目录），在哪边创建管理员、签发卡密、添加访问地址，另一边都能看到。
- 首次运行任一 `make` 命令时，若没有 `.env`，会自动生成并写入随机的 `SPARK_AUTH_SECRET_KEY`；之后 `make` 命令都读取它，保证本机与 Docker 使用同一密钥，否则一边签发的卡密在另一边会激活失败。
- `.env` 不提交，请自行备份；手动编辑时值不要加引号。直接用 `docker compose` 而不经 `make` 时，不会自动生成 `.env`。
- 不要同时运行 `make run` 和 Docker 服务（两者都占 8000 端口，且同时写同一个 SQLite 文件有损坏风险）。
- 服务监听宿主机 `127.0.0.1:8000`，由宿主机上的 nginx / caddy 对外，配置同上；要直接对局域网开放，把 `docker-compose.yml` 中的 `127.0.0.1:8000:8000` 改为 `8000:8000`。
- 容器以 uid 1000 运行；在 Linux 上若 `server/` 目录属于其它用户，需让 uid 1000 可写该目录。
- 容器内检测到的局域网 IP 是 Docker 内部地址，后台的局域网候选地址不可用；请手动填写或检测公网 IP。
- 升级：`git pull && make docker-up`。

## 目录

```text
docs/              能力文档（服务端 / 客户端规范）
server/keys/       软件与卡密批次、卡密签发、验证与导出
server/activation/ 激活记录与换设备记录、激活与校验接口、MCP
server/console/    管理后台与访问地址校验
tools/             激活测试页
```
