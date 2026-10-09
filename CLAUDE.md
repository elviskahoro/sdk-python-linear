# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on
this project.

## Issue Tracking

Use GitHub Issues for all project task tracking. Do not use Beads or `bd`.

## Build & Test

```bash
uv sync                                       # install locked deps into .venv
uv run pytest -q                              # test suite (network tests deselected)
uv run python scripts/gen_operations.py --check   # operations/*.graphql vs _spec.toml
uv run python scripts/codegen.py --check      # generated models vs pinned schema
trunk check --all                             # lint (run uv sync first: pyright/pyrefly
                                              # resolve deps through .venv)
```

## Architecture Overview

`gtm_linear` is a typed SDK over Linear's GraphQL API: `client.py` owns the
HTTP transport and the error contract, `queries.py`/`mutations.py` expose the
typed read/write paths, `pagination.py` drives cursor connections, and
`workflow.py` is the CLI-friendly facade. Everything under
`gtm_linear/_generated/` plus `gtm_linear/_schema.py` is produced by
`scripts/codegen.py` from the pinned SDL in `schema/` and the operations in
`operations/` — never edit generated files by hand; regenerate instead.

## Conventions & Patterns

- Generated code is lint-ignored (see `.trunk/trunk.yaml`); fixes belong in the
  generator, and `codegen.py --check` fails CI on drift.
- Errors raise the typed hierarchy in `exceptions.py`; no bare `KeyError`/`ValueError`
  from the transport layer.
- `ruff.toml` and `pyrightconfig.json` at the repo root are repo-local copies
  of the shared oss-linter-trunk plugin configs (once present they supersede
  the plugin's exported ones); re-copy their base sections on plugin upgrades.
- Tests intentionally exercise `_private` internals and use `assert` — scoped
  per-file ignores in `ruff.toml` cover this; do not add per-line noqas for it.

<!-- entire-graph:begin -->
This repo has the entire-graph code graph installed. Before exploring code with
grep/find/whole-file reads, read .entire/graph-agent.md — resolution-first guidance
for using graph retrieval, focused source inspection, and verification.
@.entire/graph-agent.md
<!-- entire-graph:end -->
