-include .env
export SPARK_AUTH_SECRET_KEY

VENV := .venv
PY := $(CURDIR)/$(VENV)/bin/python
DEPS := $(VENV)/.deps-installed
MANAGE := cd server && SPARK_AUTH_DEBUG=1 $(PY) manage.py

.DEFAULT_GOAL := help
.PHONY: help install migrate run test docker-up docker-down docker-logs

help:
	@echo "make run               一条命令启动开发服务（自动建虚拟环境、安装依赖、迁移数据库；监听 8000 端口）"
	@echo "make test              运行服务端全部测试"
	@echo "make install           只创建虚拟环境并安装依赖"
	@echo "make migrate           只创建或更新数据库"
	@echo "make docker-up         在 Docker 中启动开发服务（与本机共用 server/ 下的数据库与密钥）"
	@echo "make docker-down       停止 Docker 开发服务"
	@echo "make docker-logs       查看 Docker 开发服务日志"

$(DEPS): server/requirements.txt
	test -x $(PY) || python3 -m venv $(VENV)
	$(PY) -m pip install -r server/requirements.txt
	touch $@

install: $(DEPS)

migrate: $(DEPS)
	$(MANAGE) migrate

run: migrate
	$(MANAGE) runserver 0.0.0.0:8000

test: $(DEPS)
	$(MANAGE) test

docker-up:
	docker compose -f docker-compose.dev.yml up -d --build

docker-down:
	docker compose -f docker-compose.dev.yml down

docker-logs:
	docker compose -f docker-compose.dev.yml logs -f
