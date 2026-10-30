# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Removed

- The `[strawberry]` extra and `gtm_linear._schema`, the generated Strawberry
  mirror of Linear's types. Nothing in the SDK imported it. `strawberry-graphql`
  remains a dev-only codegen dependency (#155).

### Changed

- Dev-only: raised the `strawberry-graphql` codegen floor to 0.337.2 and dropped the
  unused-`List`-import prune from the Pydantic plugin (strawberry#4738). The fragment
  ordering in `codegen.py` stays: removing it reorders generated output (#203).
- Request variables are built from the generated `*Variables` models and
  type-checked. Optional arguments a caller does not pass are now omitted from
  the request instead of being sent as `null`; an explicit `None` is still sent
  as `null` (#151).
- `issue.assignee`, search-result assignees, and comment `user` are now the shared
  `User` (`UserFields`) class instead of per-fragment inline classes; fields are
  unchanged (#149).

### Added

- Generation-time deprecation check: `gen_operations.py`, `codegen.py` and the
  schema-drift test fail when an operation selects a deprecated field,
  argument, enum value or input field. Known-kept members go in
  `allow_deprecated` in `operations/_spec.toml` (#153).
- `projects`, `project <id|slug|url|name>`, `project update` (preview unless
  `--apply`; descriptions over 255 chars rejected up front), `project issues`,
  and `search --project` (#127).
- `issue update` accepts several identifiers and `--ids-file`, applying one
  payload to each with per-item results; any failed item exits 1 (#136).
- `--fields` on `search`, `issues`, and `issue` selects the `--json` keys, and
  issue JSON now includes `project` (`{id, name}` or `null`) (#134).
- `search --team` accepts a team key (any casing) or an exact team name
  (case-insensitive; ambiguous names fail listing the candidates), and
  `--state` accepts a workflow-state name or a state type the team's workflow
  uses, e.g. `started`; unknown teams or states fail before any search (#135).

### Changed

- `integration-tests.yml` now also runs on pull requests authored by the
  repo owner, resolving `LINEAR_API_KEY` unattended from the locked
  `sdk-python-linear-it` vault (approvals disabled, `**`, `*/*` and `main`
  grants; #186 briefly required a vault approval and #189 removed it);
  mutating live tests carry the new `network_writes` marker (#165).
- The `sdk-python-linear-it` vault's grants now cover slashed branch
  names: `*/*` was added and the never-matching `refs/pull/**` grant
  revoked (a PR run's branch context is the plain head-ref). Branch
  patterns do not cross `/`, so one-segment prefixes like `w-565/...`
  resolve the key unattended; deeper names (`a/b/c`) match no grant
  (#165).
- Generated enums are now `(str, Enum)`: members compare equal to their string
  value, `json.dumps` serializes them, and `isinstance(x, str)` is true (#152).
- `LinearModel` sets `validate_assignment=True`; assigning a wrong-typed value to
  a model field now raises `ValidationError` (#152). Minor version bump to 0.5.0.
- The `[strawberry]` extra now requires `strawberry-graphql>=0.336.2` (was
  0.328.0). Field descriptions are no longer scrubbed, so `gtm_linear._schema`
  carries Linear's docs.
- Generated models (`gtm_linear/_generated/*`) now render Linear's SDL type and
  field descriptions as docstrings, so `help()` and IDE hovers document every
  field on the shipped classes (#150).
- The tag-triggered PyPI publish moves from GitHub Actions (`pypi.yml`) to
  `.rwx/publish.yml`, publishing with a `PYPI_TOKEN` vault secret instead of
  trusted publishing. GitHub Actions now only runs the Pullfrog harness.
- The RWX CI pipeline `.rwx/ci.yml` is renamed `.rwx/tests.yml`, so its
  GitHub status now reads "RWX: tests.yml (pull request)" — the name says
  what the gate does (the pytest suite, the `gen_operations`/`codegen`
  drift checks, the lean wheel-install check) and matches gtm-sdk's
  `tests-*.yml` pipeline naming. `.trunk/trunk.yaml`'s merge-queue
  `required_statuses` moves in the same change; a status rename landing
  without it would stall the queue on a name that no longer posts.
- CI pytest runs through RWX Captain (`captain run <suite>`, suites in
  `.captain/config.yml`) instead of `uv run pytest` directly, gaining
  retries and per-test insights in RWX; the dev group gains
  `pytest-reportlog` for the report-log ingestion. Local runs stay
  `uv run pytest -q`.
- CI resolves dependencies under a 7-day cooldown: `[tool.uv]` gains
  `exclude-newer = "7 days"`, so `uv lock` skips releases published in the
  last week and a bump can legitimately resolve older than PyPI's latest;
  the re-lock moves strawberry-graphql 0.337.2 back to 0.336.2 (0.337.2 was
  published inside the window) (#179).
- `.rwx/lint.yml` gains a main-only `push` trigger and a manual `cli`
  dispatch, both running the new full-repo `trunk-check-full` task — a
  post-merge safety net that turns main red on drift that slipped past the
  diff-scoped PR gate (#179).

### Fixed

- The generated Strawberry schema mirror (`gtm_linear/_schema.py`, shipped with
  the `[strawberry]` extra) now declares the full argument lists on `Issue`'s
  nested collection fields — `attachments`, `comments`, `relations`, and
  `inverseRelations` — instead of dropping them. The codegen pipeline's
  argument-restoration step was scoped to the root `Query`/`Mutation` types, so
  these fields were emitted as plain argument-less attributes; a consumer
  building a GraphQL server from the shipped schema could not express
  pagination or filtering on them and queries Linear's real API validates
  (e.g. the SDK's own `ListIssueAttachments` operation) were rejected
  ({{detail_github_issue_ref}}).
- The README's "Issue shape returned by queries" block now matches the generated
  `Issue` (`IssueFields`) model: `state` is the nested `IssueFieldsState` object
  (`{id, name, type}`), not a flattened `status` string, and `priority` is
  `float`, not `int | None`. The stale block documented the pre-codegen
  hand-written model from before #13 (commit `867104f`), so `issue.status`
  raised `AttributeError` while the README promised it would work;
  `issue.state.name` is the working spelling.
- `LinearQueries.list_issue_comments_page`,
  `list_issue_attachments_page`, `list_issue_relations_page`, and
  `list_issue_inverse_relations_page` and their `iter_issue_*` wrappers now
  return a valid empty page when the issue does not exist (matching their
  docstrings) instead of raising `LinearGraphQLError`. Linear reports unknown
  `issue(id:)` identifiers as a top-level GraphQL `errors` array, which
  `LinearClient._handle_response` raises on before the methods' null-`issue`
  guard runs; the methods now mirror the `get_issue` `looks_like_not_found`
  pattern and translate that shape to the typed empty page (closes #168).

## [0.4.0] - 2026-10-09

### Added

- `LinearQueries.iter_workflow_states` and matching `LinearWorkflow` methods now
  follow workflow-state cursors automatically; `list_workflow_states` remains a
  first-page convenience method. `get_workflow_state_by_type` resolves a unique
  state across all pages and raises
  `LinearWorkflowStateLookupError` when no unique match exists.
- `LinearQueries` and `LinearWorkflow` now expose paginated issue comments,
  attachments, outgoing relations, and incoming relations.
- `LinearMutations` and `LinearWorkflow` now support creating issue attachments
  and issue relations.
- The CLI now supports issue creation, updates, and comments (previewed unless
  `--apply` is provided), plus `--priority`, `--assignee`, `--label`, and
  `--state` issue-list filters and `--all` for issue listings and search.

### Changed

- JSON-mode write failures and stalled paginated reads now emit structured error
  envelopes on stdout while retaining exit code 1.
- `LinearQueries` and `LinearWorkflow` issue, search, and workflow-state iterators
  now reject non-positive `page_size` and negative `limit` eagerly. `limit=0`
  remains valid and yields no results.

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

- The `iter_issue_comments`, `iter_issue_attachments`, `iter_issue_relations`,
  and `iter_issue_inverse_relations` iterators now reject non-positive `page_size`
  and negative `limit` eagerly via `_validate_pagination_options`, matching the
  existing `iter_issues`/`iter_search_issues`/`iter_workflow_states` iterators.
  Previously `page_size <= 0` was forwarded to the API (raising
  `LinearGraphQLError` after a wasted round trip) and `limit < 0` was silently
  swallowed by `paginate`'s early return.
- The PyPI `Development Status` classifier now reads `3 - Alpha` (#69),
  matching the status the README and changelog have claimed since the 0.2.x
  line. PyPI serves classifiers from the published wheel's metadata, so the
  mismatch stays visible on the 0.3.0 page (which cannot be re-served) until
  the next version publishes.
- Renovate now updates the PyPI publisher module's SemVer release and commit
  SHA together while the build remains pinned to the immutable SHA. The
  publish-time consistency check is offline; the weekly RWX drift check still
  verifies upstream tags and reports stale releases.

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
