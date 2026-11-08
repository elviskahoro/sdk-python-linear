---
name: rwx
description: Use the RWX CLI in this repo - authenticate with the injected per-org tokens, run `.rwx/*.yml` pipelines (including against uncommitted changes), read results/logs/artifacts, lint and author RWX configs, and manage vaults. Use when asked about RWX, when CI is red or failing, to run tests/type-checks on RWX, to run the live-API tests, or to fetch RWX logs.
allowed-tools: Bash(rwx lint *), Bash(rwx docs *), Bash(rwx logs *), Bash(rwx artifacts *), Bash(rwx results *), Bash(rwx runs list *), Bash(rwx whoami)
---

# RWX CLI in this repo

CI definitions live in `.rwx/`:

| File | Trigger | Purpose |
| --- | --- | --- |
| `tests.yml` | github push (main), pull_request, weekly cron, cli | pytest through Captain, codegen checks |
| `lint.yml` | github pull_request, push (main), cli | Trunk lint |
| `integration-tests.yml` | github push (main), pull_request (owner-authored PRs), weekly cron, cli | live-API tests (`-m network`), needs vault key |
| `publish.yml` | github push (tags `v*`), cli | release build |
| `dagger-ref-drift.yml` | cron (weekly), cli, dispatch | Dagger ref drift check |
| `pyright.yml`, `pyrefly.yml` | `cli` only | on-demand type checks, never run on PRs |

## Auth: one injected token per RWX organization

List token names (never values):

    env | grep -E '^RW[XC]_ACCESS_TOKEN_' | cut -d= -f1

Names look like `RWX_ACCESS_TOKEN_<ORG>` (prefix may be `RWC_`); `<ORG>` is the
uppercased org. This repo belongs to `elviskahoro`, so use `*_ELVISKAHORO`. The
CLI reads only `RWX_ACCESS_TOKEN` (or `--access-token`), so pass it per command
instead of exporting, and never print it:

    RWX_ACCESS_TOKEN="$RWX_ACCESS_TOKEN_ELVISKAHORO" rwx whoami   # confirm org

Run, task and log IDs only resolve under the org that owns them; on
not-found/unauthorized, try the other token. Never run `rwx ... --help` with real
credentials in the environment without `env -u <VAR>` (clap prints `[env: ...]`
defaults).

`allowed-tools` pre-approves only plain read-only `rwx` calls. Commands with an
env-var prefix, a pipeline or `mktemp` (including the token-selection and log
examples here) may still prompt for approval.

## Running a pipeline

`rwx run` sends a git patch, so no commit or push is needed (HEAD's closest
ancestor on `origin` must exist). The patch includes unpushed commits, staged,
unstaged and untracked files; `.gitignore`d files are excluded and Git LFS
changes error. `RWX_DISABLE_GIT_PATCH=1` turns it off.

    rwx run .rwx/tests.yml --wait --fail-fast
    rwx run .rwx/tests.yml --target <task-key> --wait   # one task + deps
    rwx run .rwx/pyright.yml --wait --init paths="gtm_linear/client.py"
    rwx run .rwx/pyrefly.yml --wait --init paths="gtm_linear/client.py"
    rwx run .rwx/integration-tests.yml --wait           # live API, vault key

Keep `commit-sha: ${{ event.git.sha }}` in each `on:` trigger and
`ref: ${{ init.commit-sha }}` on `git/clone`; that wiring makes the patch apply.
Use `--json` to parse, `--title` to label.

## Results, logs, artifacts

    rwx runs list
    rwx results <run-id>                                   # status + failures
    rwx logs <run-id> --task <task-key> --output "$(mktemp -d)"
    rwx logs <task-id>
    rwx artifacts --help

A green run is not proof a step worked. Read the logs for steps that talk to
third parties (Trunk prints `Error Encountered`; an empty JUnit glob uploads
nothing and passes unless `--allow-empty-test-results=false`).

## Authoring and debugging

    rwx lint .rwx/<name>.yml                    # run after every edit
    rwx docs pull /migrating/rwx-reference      # syntax reference; do not truncate
    rwx docs search "<query>"; rwx docs pull <path>
    rwx sandbox exec -- <command>               # persistent sandbox
    rwx retry <run-id> / rwx debug <task-id> / rwx ssh <task-id>

Read docs with `rwx docs pull` via Bash, not WebFetch. Also available:
`rwx resolve` / `rwx update` (package and base-image versions), `rwx cancel`,
`rwx dispatch`, `rwx crons`.

## Vaults and secrets

`LINEAR_API_KEY` lives in the `sdk-python-linear-it` vault. A vault needs repo
grants `REPO:**`, `REPO:*/*`, `REPO:main`, "Allow public repositories" on
(UI-only), and for `rwx run` dispatches `rwx vaults access allow --vault <name>
--email <user>`. Branch patterns do not cross `/`. Do not read secrets, and
confirm with the user before changing grants. Details: `.entire/agent-guide.md`
("RWX access", "Creating a vault").
