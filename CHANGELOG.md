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
  covering those. See `.rwx/.migration-inventory.md` for the flip status,
  including the two remaining setup steps (GitHub App wiring and the vault
  token swap).

### Added

- RWX pipeline configs under `.rwx/` — `.rwx/ci.yml` (tests, codegen
  `--check`s, lean install, weekly schema-drift cron) and
  `.rwx/dagger-ref-drift.yml` (weekly pinned-Dagger-module-ref drift check)
  — porting the GitHub Actions workflows to RWX following gtm-sdk's pilot
  pattern. GitHub Actions remains the canonical CI gate until the RWX
  pipelines are validated and flipped; see `.rwx/.migration-inventory.md`
  for the port inventory, the deliberate out-of-scope calls (pypi.yml,
  pullfrog.yml), and the flip checklist.

### Changed

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
