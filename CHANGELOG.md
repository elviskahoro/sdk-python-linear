# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- Dependabot version updates now observe a 7-day cooldown
  (`cooldown.default-days: 7`) before proposing updates for newly published
  GitHub Actions versions, so freshly published — possibly malicious or
  unstable — tags are not picked up immediately (Dependabot's implicit
  default is 3 days).

### Removed

- `scripts/trunk-lint-file-beads.py` — the beads-filing lint sweep ported
  from gtm-sdk in 0.3.0 without its `scripts/lib` support modules or the
  `dagger` dev dependency, so it never ran in this repo; and it files beads,
  the tracker this repo has since replaced with GitHub Issues.

### Fixed

- The PyPI `Development Status` classifier now reads `3 - Alpha` (#69),
  matching the status the README and changelog have claimed since the 0.2.x
  line. PyPI serves classifiers from the published wheel's metadata, so the
  mismatch stays visible on the 0.3.0 page (which cannot be re-served) until
  the next version publishes.

## [0.3.0] - 2026-10-05

First PyPI release that ships the CLI: the `v0.2.2` tag was cut before the
CLI merged, so its wheel installs no executable and `uvx gtm-linear` reports
"Package `gtm-linear` does not provide any executables." Install `0.3.0`
or later for the `gtm-linear` command.

### Added

- A read-only CLI, installed as the `gtm-linear` console command: `viewer`
  (auth check), `teams`, `issues --team ENG`, `issue ENG-123`, and
  workspace-wide `search`. Every command takes `--json`; `--limit` is
  validated to 1-100 at parse time; auth resolves `LINEAR_API_KEY` from the
  environment or a `.env` / `.env.local` file in the working directory
  (endpoint overrides come from the real environment only). The failure
  contract is a single red, sanitized stderr line per error and exit code 1
  (2 for usage errors, 130 for Ctrl-C) — never a traceback. The CLI is
  deliberately read-only; writes stay in the SDK (`LinearMutations`) so a
  shell typo can never mutate Linear.
- `typer>=0.27` joins the runtime dependencies for the CLI. Only
  `gtm_linear/cli.py` imports it — `import gtm_linear` never touches it —
  and it is unconditional rather than a `[cli]` extra so `uvx gtm-linear`
  and `uv tool install gtm-linear` need no extra syntax.
- MCP server configuration (`.mcp.json`) — the Corridor HTTP MCP server,
  authenticated via `CORRIDOR_API_TOKEN` from the environment.
- RWX pipeline configs under `.rwx/` — `.rwx/ci.yml` (tests, codegen
  `--check`s, lean install, weekly schema-drift cron) and
  `.rwx/dagger-ref-drift.yml` (weekly pinned-Dagger-module-ref drift check)
  — porting the GitHub Actions workflows to RWX following gtm-sdk's pilot
  pattern. The pilot has since been validated and flipped: RWX is the
  canonical CI gate, and GitHub Actions remains only for the publish and
  agent workflows; see `.rwx/.migration-inventory.md`
  for the port inventory and the deliberate out-of-scope calls (pypi.yml,
  pullfrog.yml).
- `scripts/trunk-lint-file-beads.py` — files beads issues straight from
  trunk lint findings.

### Changed

- The `[strawberry]` extra's floor rises from `strawberry-graphql>=0.316.0`
  to `>=0.328.0`. 0.316's metadata wrongly permits graphql-core 3.3, which
  breaks the generated `_schema` module at import; 0.328 is the release line
  the module is generated and CI-verified against, and the raised floor
  keeps the extra from resolving onto the broken pair.
- Generated scalar types migrate off the deprecated `strawberry.scalar()`
  class form, keeping the `[strawberry]` mirror importable on the 0.328+
  release line.
- `LinearClient` — and therefore `LinearWorkflow`, `LinearClient.from_env`,
  and `LinearClient.from_settings` — validates the API key at construction
  instead of failing at the first request deep inside httpx. Surrounding
  whitespace is stripped, so a trailing newline attached by a secret store no
  longer crashes with `httpx.LocalProtocolError: Illegal header value`.
- Blank keys, keys with embedded whitespace/control/non-ASCII characters,
  and non-string keys (for example a `LinearClient` or `LinearSettings`
  object passed by mistake) now raise `ValueError` / `TypeError` immediately,
  with the message naming the offending type and the fix. `bytes` keys,
  which the signature never promised but runtime happened to accept, now
  raise `TypeError` — decode to `str` first.
- The weekly dagger-ref-drift check reads its GitHub token from a locked,
  repo-scoped `sdk-python-linear` RWX vault (fine-grained PAT with Issues:
  Read/Write on this repo only) instead of the shared default-vault
  secret, and gained a `dagger-ref-drift` dispatch trigger —
  `rwx dispatch dagger-ref-drift --ref main` reproduces the weekly cron's
  exact path (same repository, ref, and vault unlock) for on-demand checks.
- Pin the PyPI publisher Dagger module to its `v0.2.2` release, protect
  version tags upstream, and have the weekly drift check flag only newer
  stable releases.
- README aligned with the current repo — status, install, CLI, mental model,
  layout, and conventions sections now describe what actually ships.

### Removed

- The `ci.yml` and `dagger-ref-drift.yml` GitHub Actions workflows — their
  RWX ports (`.rwx/ci.yml`, `.rwx/dagger-ref-drift.yml`) are now the
  canonical CI and drift checks, validated against live cloud dispatches on
  both trigger paths before the flip. The drift cron's pilot stagger is
  reverted to the original 09:00 UTC. GitHub Actions remains for `pypi.yml`
  (PyPI trusted publishing) and `pullfrog.yml`; dependabot.yml keeps
  covering those. See `.rwx/.migration-inventory.md` for the full record —
  the two setup steps it flagged as remaining (GitHub App wiring, vault
  token swap) have since been completed and verified with live runs.
