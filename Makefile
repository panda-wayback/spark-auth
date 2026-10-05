-include .env
export SPARK_AUTH_SECRET_KEY

.env:
	@echo "SPARK_AUTH_SECRET_KEY=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" > $@
	@echo "已生成 .env（随机 SPARK_AUTH_SECRET_KEY）。请妥善备份：丢失或更换会使未激活卡密与已签发 token 失效。"

VENV := .venv
PY := $(CURDIR)/$(VENV)/bin/python
MANAGE := cd server && SPARK_AUTH_DEBUG=1 $(PY) manage.py

.DEFAULT_GOAL := help
.PHONY: help install migrate superuser run tester test docker-up docker-down docker-logs

help:
	@echo "make install           创建虚拟环境并安装依赖"
	@echo "make migrate           创建或更新数据库"
	@echo "make superuser         创建管理员账号"
	@echo "make run               以开发模式启动服务端（监听所有网卡的 8000 端口；局域网 IP 需在后台“访问地址”页添加）"
	@echo "make tester            启动激活码测试页（http://127.0.0.1:8002/，转发到 8000）"
	@echo "make test              运行服务端全部测试"
	@echo "make docker-up         构建并在 Docker 中启动服务（监听 127.0.0.1:8000；与本机共用 server/ 下的数据库和访问地址文件）"
	@echo "make docker-down       停止 Docker 服务"
	@echo "make docker-logs       查看 Docker 服务日志"

$(PY):
	python3 -m venv $(VENV)

install: $(PY)
	$(PY) -m pip install -r server/requirements.txt

migrate:
	$(MANAGE) migrate

superuser:
	$(MANAGE) createsuperuser

run:
	$(MANAGE) runserver 0.0.0.0:8000

tester:
	$(PY) tools/activation_tester.py

test:
	$(MANAGE) test

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f
