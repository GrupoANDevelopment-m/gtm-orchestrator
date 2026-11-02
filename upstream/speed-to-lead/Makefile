.DEFAULT_GOAL := help
.PHONY: help install demo serve test lint fmt typecheck check train eval docker-up clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install deps (dev + llm extras)
	uv sync --extra dev --extra llm

demo: ## Run sample leads through the pipeline — keyless, no signups
	uv run speed-to-lead demo

serve: ## Run the API at http://127.0.0.1:8000
	uv run speed-to-lead serve

test: ## Run the test suite
	uv run pytest -q

lint: ## Lint with ruff
	uv run ruff check .

fmt: ## Auto-format with ruff
	uv run ruff format .

typecheck: ## Static type-check with mypy (strict)
	uv run mypy

check: lint typecheck test ## Lint + types + tests (what CI runs)

train: ## Fine-tune the LoRA intent classifier (needs the `ml` extra)
	uv run --extra ml python -m speed_to_lead.ml.train

eval: ## Evaluate the classifier vs the LLM/rule baseline
	uv run --extra ml python -m speed_to_lead.ml.evaluate

docker-up: ## Start the full stack (api + worker + postgres + redis)
	docker compose up --build

clean: ## Remove caches and build artifacts
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dist build
