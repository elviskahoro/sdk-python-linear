# Agent Instructions

Use GitHub Issues for all project task tracking. Create, update, label, and
close issues in the repository's GitHub project. Do not use Beads or `bd`.

## Non-Interactive Shell Commands

Always use non-interactive flags with file operations to avoid confirmation
prompts. For example, use `cp -f`, `mv -f`, `rm -f`, and `rm -rf` where the
target is explicit and the operation is intended.

For commands that may prompt, use non-interactive options such as
`BatchMode=yes` for SSH/SCP and `-y` for package installation.

## Build & Test

```bash
uv sync                                       # install locked deps into .venv
uv run pytest -q                              # test suite (network tests deselected)
uv run python scripts/gen_operations.py --check   # operations/*.graphql vs _spec.toml
uv run python scripts/codegen.py --check      # generated models vs pinned schema
trunk check --all                             # lint (run uv sync first: pyright/pyrefly
                                              # resolve deps through .venv)
```

CI runs pytest through Captain (`captain run <suite>`, suites in
`.captain/config.yml`); locally keep using `uv run pytest -q`.

## Landing PRs

PRs land through the Trunk Merge Queue, not the GitHub Merge button.

- Submit only when the user has approved landing — an explicit ask, or the
  create_pr preview approval that says the PR will be queued. Queueing
  auto-merges once CI passes, so submitting is merging; otherwise stop
  after opening the PR. Submit with `trunk merge <pr-number>` (a number,
  not a URL or branch name).
- Check once: `trunk merge status <pr-number>`. States: Queued, Testing,
  Tests Passed, Merged. Report the state and stop. Never poll in a loop.
- Never use `gh pr merge` or the GitHub Merge button. They bypass the queue.
- Never use `--force`, `pause` or `resume`.
- Leave priority at the default (`medium`). `urgent` and `high` are for
  incidents and need an explicit reason from the user.
- Failed or PENDING_FAILURE: PENDING_FAILURE may be a flake, so re-run the
  same `trunk merge <pr-number>` submit once. Failed means push a fix to
  the PR branch and resubmit. Do not resubmit blindly in a loop; after one
  retry, report the state and let the user decide.
- Auth failure: run `trunk login` once and retry.
- Queue not active (for example "Merge queue does not exist for your repo"):
  stop and report that the PR is open but not queued. Never fall back to
  `gh pr merge` or the GitHub Merge button.
- Refuse to queue from main or a protected branch.
- A `Closes #N` line in the PR body auto-closes the issue when the queue lands
  the PR. Do not close the issue by hand beforehand.

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
- RWX CI changes: a green run is not proof a step worked. Read the task's
  logs (`rwx logs`) for steps that talk to third parties; the Trunk CLI
  prints `Error Encountered` and still exits 0. The workspace has no `.git`,
  so `trunk flakytests upload` needs `--use-uncloned-repo` plus the
  `--repo-*` overrides. A new vault must have "Allow public repositories" on
  (UI-only) and, for `rwx run` dispatches, an `rwx vaults access allow
  --email` entry for the account user. See `.entire/agent-guide.md`,
  "Creating a vault".



<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->

Live-API tests (`-m network`) need the vault key: push the branch, then
`rwx run .rwx/integration-tests.yml --wait` (see `.entire/agent-guide.md`,
"RWX access").
