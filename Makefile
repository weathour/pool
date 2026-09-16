# 池子 —— 常用命令
#
#   make check     跑全部测试（单元 + 端到端）
#   make validate  只校验账本
#   make status    一眼看清现状
#   make site      生成展示页到 site/
#   make clean     清理构建产物

.PHONY: check validate status site clean

PY := python3

check:
	@echo "=== 单元测试 ==="
	@$(PY) tests/test_units.py
	@echo
	@echo "=== 端到端自测 ==="
	@$(PY) selftest.py

validate:
	@$(PY) tools/validate

status:
	@$(PY) tools/validate --quiet
	@$(PY) tools/status

site:
	@$(PY) tools/build-site

clean:
	rm -rf site .index
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理"

help:
	@head -8 Makefile | tail -7
