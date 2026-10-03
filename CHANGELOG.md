# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

### Added

- RWX pipeline configs under `.rwx/` — `.rwx/ci.yml` (tests, codegen
  `--check`s, lean install, weekly schema-drift cron) and
  `.rwx/dagger-ref-drift.yml` (weekly pinned-Dagger-module-ref drift check)
  — porting the GitHub Actions workflows to RWX following gtm-sdk's pilot
  pattern. The pilot has since been validated and flipped: RWX is the
  canonical CI gate, and GitHub Actions remains only for the publish and
  agent workflows; see `.rwx/.migration-inventory.md`
  for the port inventory and the deliberate out-of-scope calls (pypi.yml,
  pullfrog.yml).

### Changed

- The weekly dagger-ref-drift check reads its GitHub token from a locked,
  repo-scoped `sdk-python-linear` RWX vault (fine-grained PAT with Issues:
  Read/Write on this repo only) instead of the shared default-vault
  secret, and gained a `dagger-ref-drift` dispatch trigger —
  `rwx dispatch dagger-ref-drift --ref main` reproduces the weekly cron's
  exact path (same repository, ref, and vault unlock) for on-demand checks.
- Pin the PyPI publisher Dagger module to its `v0.2.2` release, protect
  version tags upstream, and have the weekly drift check flag only newer
  stable releases.
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
