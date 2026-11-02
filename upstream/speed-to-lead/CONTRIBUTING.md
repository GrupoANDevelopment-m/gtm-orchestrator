# Contributing

Thanks for your interest in improving **speed-to-lead-agent**.

## Setup

```bash
uv sync --extra dev          # create the venv + install
uv run pre-commit install    # optional: lint/format on commit
make demo                    # sanity check — runs keyless
```

## Before you open a PR

Run the same gate CI runs:

```bash
make check                   # ruff + mypy (strict) + pytest
```

- **Keep it typed.** `mypy --strict` must pass; no new `# type: ignore` without a reason.
- **Add a test** for any behavior change (`tests/`).
- **No secrets, ever.** Configuration is bring-your-own-key via `.env` (gitignored). Never hardcode a
  key or commit `.env`.
- **Integrations are pluggable.** New CRM/notify/enrich providers should implement the existing
  protocol and degrade gracefully when their key is absent.
- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`…) keep the history readable.

## Architecture in one breath

A webhook enqueues a lead; a worker runs it through a LangGraph pipeline
(`research → qualify → draft → route`). Services and integrations sit behind small protocols so any
piece can be swapped without touching the graph. See [`README.md`](README.md) and
[`docs/`](docs/) for the deeper tour.
