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

## RWX CLI

CI runs on RWX (`.rwx/`). Auth uses per-org tokens injected as
`RWX_ACCESS_TOKEN_<ORG>`; this repo uses the `ELVISKAHORO` one, passed per
command (`RWX_ACCESS_TOKEN="$RWX_ACCESS_TOKEN_ELVISKAHORO" rwx whoami`), never
printed. `rwx run .rwx/<file>.yml --wait` runs a pipeline against the working
tree without committing; `rwx results`/`rwx logs` read output; `rwx lint`
validates configs. Full guide: `.agents/skills/rwx/SKILL.md`.

## Landing PRs

PRs land with a squash merge through the GitHub CLI.

- Merge only when the user has approved landing — an explicit ask, or a
  yes after CI is green. Merging is never automatic and is
  irreversible, so otherwise stop after opening the PR.
- Merge with `gh pr merge <pr-number> --squash` (a number, not a URL or
  branch name). Always pass `--squash`; never `--merge` or `--rebase`.
  Never pass `--auto` unless the user asks for it.
- Check once: `gh pr view <pr-number> --json state,mergeStateStatus` (or
  `gh pr checks <pr-number>`). Report the state and stop. Never poll in a
  loop.
- Never use `--admin` to bypass branch protection.
- If the merge is blocked (failing checks, required review, conflicts),
  report the blocker and let the user decide; push a fix to the PR branch
  if asked.
- Refuse to merge from main or a protected branch.
- A `Closes #N` line in the PR body auto-closes the issue when the PR
  merges. Do not close the issue by hand beforehand.

## Architecture Overview

`gtm_linear` is a typed SDK over Linear's GraphQL API: `client.py` owns the
HTTP transport and the error contract, `queries.py`/`mutations.py` expose the
typed read/write paths, `pagination.py` drives cursor connections, and
`workflow.py` is the CLI-friendly facade. Everything under
`gtm_linear/_generated/` is produced by
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
  prints `Error Encountered` (usually with a nonzero exit, but check the
  banner too), and an empty JUnit glob uploads nothing and passes unless
  `--allow-empty-test-results=false`. The workspace has no `.git`, so
  `trunk flakytests upload` needs `--use-uncloned-repo` plus the `--repo-*`
  overrides. A new vault must have "Allow public repositories" on
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

## Pullfrog (automated CI fixes)

Pullfrog may push fix commits to PR branches. CI stays the independent gate
(see elviskahoro/flox-conductor-sandbox#111):

- Before pushing, run `uv run pytest -q`; do not push a fix you have not run.
- Make at most 3 automatic fix attempts per PR, then stop and comment with
  what failed. Do not retry the same fix in a loop.
- Never weaken, skip, delete, or loosen tests/assertions to get a check green.
  If a test looks flaky or wrong, say so in a comment and leave it to a human.
- Do not edit `.rwx/`, `.github/`, vault or branch-protection configuration.
- Do not add secrets or touch the `sdk-python-linear-it` vault. Integration
  tests are gated to PRs authored by the repo owner (the gate in
  `.rwx/integration-tests.yml` checks the PR author, not the commit author),
  so they skip Pullfrog-authored PRs — but a Pullfrog-pushed fix commit on a
  human-authored PR still triggers the live-API suite and resolves the vault
  key unattended, which is why the pre-push `pytest -q` applies to every
  push, not only Pullfrog-authored ones.
