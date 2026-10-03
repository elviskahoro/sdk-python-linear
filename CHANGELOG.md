# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
