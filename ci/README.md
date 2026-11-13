# ci/: portable CI core (Dagger SDK program, no module)

`ci/main.py` is a plain Python program using `dagger-io==0.21.8` (no
`dagger.json`/`dagger.toml`). The same code runs locally and in RWX.

## Run locally (needs Docker + `dagger` CLI 0.21.8)

    ci/run.sh test           # pytest (+1 retry of failures) + gen_operations/codegen --check
    ci/run.sh lean-install   # wheel builds/imports with runtime deps only
    ci/run.sh all

JUnit XML lands in `tmp/` (override with `CI_OUT_DIR`). No secrets are needed.
Debug with the `dagger run` TUI output, or `rwx logs <task-id>` in CI.

## Classification

| Layer | What | Where |
| --- | --- | --- |
| A | uv, Dagger CLI (sha256-pinned), `ci/` venv, engine image warm, `deps` for schema-drift | `.rwx/tests.yml` (content-cached) |
| B | triggers, trunk-upload, `test` re-fail gate, schema-drift, integration-tests, publish, drift, trunk lint | `.rwx/*.yml` (unchanged) |
| C | pytest, `--check` scripts, lean-install | `ci/main.py` via `ci/run.sh` |


## Cache behavior

Dagger-in-container uses its own layer cache, which is discarded with the RWX
runner, so a Dagger task that re-runs rebuilds its venv (uv download cache is a
Dagger cache volume, not persisted across runs; RWX `tool-cache` is not wired
up yet). The RWX tasks before it stay content-cached: unchanged commit = all
hits; any file change in the tree re-runs `pytest` and `lean-install` only
(`lean-install` has an RWX `filter` on wheel inputs + `ci/`); a `ci/uv.lock` change re-runs
`dagger-sdk-install`.

## Cloud sandboxes

Dagger cannot run in Conductor cloud sandboxes (no Docker): validate with
`rwx run .rwx/tests.yml --wait`.
