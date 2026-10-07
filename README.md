# Spark Auth

个人用的软件激活服务端：按软件签发自包含卡密，用户在软件里输入卡密后绑定到一台设备。

- 卡密约 28 个字符、便于手输，难以猜测、无法伪造；服务端只保存已激活的记录。
- 每次签发生成一个新的卡密批次；禁用批次只阻止新激活，已激活设备不受影响。
- 一个卡密同一时刻只绑定一台设备，可按软件设置是否允许换设备、换一次扣多少时长。
- 也可新建「按次数」软件：卡密由服务后端核销，签发时设定可用次数，每次扣 1 次、用完作废（如网盘代下载按次收费）。
- 极简网页后台管理一切；客户端不在本仓库，接入方只提交卡密，不需要传软件标识。

技术栈：Python + Django + SQLite。

## 快速开始

```bash
make run         # 自动建 .venv、安装依赖、迁移数据库，然后启动开发服务（8000 端口）
```

或者用 Docker（无需本机 Python）：

```bash
docker compose up -d
```

浏览器打开 <http://127.0.0.1:8000/>，首次访问会进入初始化页创建管理员，然后：新建软件 → 管理卡密 → 签发卡密。

测试卡密：在软件的卡密页「测试卡密」中粘贴卡密，即可直接测试激活、校验或核销。其它命令：`make test` 运行测试。

## 客户端接入

- 激活：`POST /api/activate`，校验：`POST /api/verify`，详见 [docs/client/activation](docs/client/activation/README.md)。
- 按次数卡密核销：`POST /api/redeem`，详见 [docs/client/redeem](docs/client/redeem/README.md)。
- 让 AI 写接入代码：在后台「MCP 接入」页复制配置到接入方项目的 `.cursor/mcp.json`，AI 即可拿到服务地址、接口与接入规范。

## 部署

```bash
export SPARK_AUTH_DB_PATH=/path/to/db.sqlite3        # 可选，默认 server/db.sqlite3
cd server && ../.venv/bin/python manage.py migrate
../.venv/bin/gunicorn config.wsgi -b 127.0.0.1:8000 --workers 2
```

- 启动后浏览器打开服务地址，首次访问会进入初始化页创建管理员。
- 服务端密钥：未设置 `SPARK_AUTH_SECRET_KEY` 时，首次启动在数据库同目录生成 `secret_key` 文件并一直沿用；备份时连同数据库一起备份。丢失或更换密钥会使未激活卡密与已签发 token 全部失效。
- 想让"只泄露数据库也无法伪造卡密"成立，就用环境变量 `SPARK_AUTH_SECRET_KEY` 单独提供密钥，不要和数据库放在一起。
- 健康检查：`GET /healthz`，数据库可用时返回 200。

- gunicorn 只监听本机，由 nginx / caddy 对外并配置域名。
- nginx 需转发 `Host`、协议头与客户端 IP（激活与核销记录的 IP 取自 `X-Forwarded-For`）；caddy 默认已转发：

  ```nginx
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  ```

- 部署时带上 `docs/` 目录（MCP 接入说明从中读取）。
- 开发模式需设置 `SPARK_AUTH_DEBUG=1`（`make` 命令已自动设置），生产环境不要设置。

## Docker 部署

先构建镜像（暂不发布公共仓库，可推送到自己的私有仓库）：

```bash
docker build -t spark-auth:latest .
```

运行只需一个数据卷，不需要任何环境变量：

```bash
docker run -d --name spark-auth --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v spark-auth-data:/data \
  spark-auth:latest
```

在其它项目的 compose 中作为依赖（等 spark-auth 健康后再启动自己的服务）：

```yaml
services:
  auth:
    image: spark-auth:latest
    restart: unless-stopped
    volumes:
      - auth-data:/data
  app:
    image: my-app:latest
    environment:
      SPARK_AUTH_URL: http://auth:8000
    depends_on:
      auth:
        condition: service_healthy

volumes:
  auth-data:
```

- 容器启动时自动迁移数据库；浏览器打开服务地址，首次访问进入初始化页，填写用户名和密码即可创建管理员并自动登录。
- 管理员创建后初始化页永久失效，之后只能在登录页登录；密码可在后台修改。
- 数据卷 `/data` 中保存数据库 `db.sqlite3` 与自动生成的服务端密钥 `secret_key`，备份或迁移数据卷即可；容器以 uid 1000 运行。
- 镜像内置健康检查（访问 `/healthz`），`docker ps` 可看到 `healthy` / `unhealthy`。
- 一个数据卷只给一个容器用；多个项目各起各的实例、各用各的数据卷。
- 仓库根目录的 `docker-compose.yml` 就是上面单实例的写法，`docker compose up -d` 会构建并启动。

本仓库内的开发式部署（与本机共用 `server/` 下的数据库与密钥）：

```bash
make docker-up          # 使用 docker-compose.dev.yml 构建并启动
```

- 其它命令：`make docker-logs` 查看日志，`make docker-down` 停止。
- 若根目录有 `.env` 并设置了 `SPARK_AUTH_SECRET_KEY`，`make` 与开发 compose 都优先使用它（兼容旧环境）；否则使用 `server/secret_key`。
- 不要同时运行 `make run` 和 Docker 服务（两者都占 8000 端口，且同时写同一个 SQLite 文件有损坏风险）。
- 升级：`git pull && make docker-up`。

## 目录

```text
docs/              能力文档（服务端 / 客户端规范）
server/keys/       软件与卡密批次、卡密签发、验证与导出
server/activation/ 激活记录与换设备记录、激活与校验接口、MCP
server/redeem/     按次数卡密核销记录、核销接口
server/console/    管理后台
```
