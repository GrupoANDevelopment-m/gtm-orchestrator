PYTHON ?= python3
PORT ?= 8002
PYTEST ?= python3 -m pytest

.PHONY: help install test test-unit test-integration test-all server stop clean ci lint

help:
	@echo "GTM Orchestrator"
	@echo "  make install        Install deps"
	@echo "  make test-unit      Unit tests (no server)"
	@echo "  make test-integration Integration tests (server must run)"
	@echo "  make test           All tests"
	@echo "  make server         Start server on PORT (default 8002)"
	@echo "  make stop           Stop server"
	@echo "  make lint           Check syntax + UI for emojis"
	@echo "  make ci             lint + test-unit + test-integration"

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

test: test-all

test-unit:
	$(PYTEST) tests/test_text_sanitizer.py -v

test-integration:
	$(PYTEST) tests/test_http_endpoints.py tests/test_specific.py -v

test-all:
	$(PYTEST) tests/ -v

server:
	@mkdir -p data keys
	@cd server && nohup $(PYTHON) -B api.py $(PORT) > ../server.log 2>&1 &
	@sleep 8
	@curl -s -o /dev/null -w "Health: %{http_code}\n" http://127.0.0.1:$(PORT)/health

stop:
	@pkill -9 -f "python3 -B server/api.py" 2>/dev/null || true
	@echo "Stopped."

lint:
	@$(PYTHON) scripts/lint.py

ci: lint test-unit test-integration
	@echo ""
	@echo "  CI passed."
