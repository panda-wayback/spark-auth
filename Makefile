VENV := .venv
PY := $(CURDIR)/$(VENV)/bin/python
MANAGE := cd server && SPARK_AUTH_DEBUG=1 $(PY) manage.py

.DEFAULT_GOAL := help
.PHONY: help install migrate superuser run test spec

help:
	@echo "make install    创建虚拟环境并安装依赖"
	@echo "make migrate    创建或更新数据库"
	@echo "make superuser  创建管理员账号"
	@echo "make run        以开发模式启动服务端（http://127.0.0.1:8000/）"
	@echo "make test       运行服务端全部测试"
	@echo "make spec       检查 spec.yaml 索引"

$(PY):
	python3 -m venv $(VENV)

install: $(PY)
	$(PY) -m pip install -r server/requirements.txt pyyaml

migrate:
	$(MANAGE) migrate

superuser:
	$(MANAGE) createsuperuser

run:
	$(MANAGE) runserver

test:
	$(MANAGE) test

spec:
	$(PY) .cursor/skills/pseudo-software/scripts/check_index.py
