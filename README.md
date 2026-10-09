# gtm-linear

Async-first Python SDK for the [Linear](https://linear.app) GraphQL API. Thin, typed wrapper around `httpx` with optional sync support, Strawberry-typed models, explicit error semantics, and a bundled read-only CLI.

> **Status:** Alpha (`0.4.0`, see `pyproject.toml` and `CHANGELOG.md`). API surface is small but incomplete — fall back to raw `LinearClient.execute_async` for anything not yet wrapped.

---

## When to use this (agent triage)

| Situation | Use this SDK? |
| --- | --- |
| Read/write Linear issues from Python with typed responses | Yes |
| Need ad-hoc GraphQL escape hatch alongside typed helpers | Yes — `LinearClient.execute_async(query, variables)` |
| Building MCP-style tooling against Linear | Yes (low-level), or prefer the official Linear MCP server for higher-level intent |
| Need full coverage of Linear's GraphQL schema | **No** — only a focused subset of Linear types is wrapped today |
| Need webhooks, OAuth flow, or attachments | **No** — not implemented |
| Writing a one-off shell command | Yes — the bundled CLI: `uvx gtm-linear issues --team ENG` |

If you only need to *create or read a few issues* from an automation, this is the right tool. If you need broad schema coverage, drop down to `execute_async` with a hand-written query.

---

## Install

```bash
uv pip install gtm-linear        # from PyPI (>= 0.3.0)
# or, in this repo:
uv sync
```

Installing the package also installs the `gtm-linear` console command — see [CLI](#cli).

Requires Python `>=3.11`. Runtime deps: `httpx>=0.27`, `pydantic>=2.0`, `pydantic-settings>=2.14.2`, `typer>=0.27` (imported only by the CLI module — `import gtm_linear` never touches it). Typer is unconditional rather than a `[cli]` extra so `uvx gtm-linear` and `uv tool install gtm-linear` need no extra syntax — a deliberate install-footprint trade-off for library-only consumers. The optional `[strawberry]` extra (ships the generated schema mirror) adds `strawberry-graphql>=0.328.0`, which pulls `graphql-core>=3.3,<3.4`.

---

## Auth

Linear personal API key. Format: `lin_api_...`. Pass the raw key as the `Authorization` header value (no `Bearer ` prefix — Linear accepts the key directly).

```bash
export LINEAR_API_KEY=lin_api_xxx
```

The SDK does not read env vars on its own. Caller is responsible for passing `api_key=` to `LinearClient`. The key is stripped of surrounding whitespace at construction — a trailing newline from a secret store is harmless instead of an `Illegal header value` crash — and anything that is not a `str`/`SecretStr` (a `LinearClient` or `LinearSettings` object, say) raises `TypeError` immediately rather than failing later inside httpx. Blank or corrupted keys (embedded whitespace, control, or non-ASCII characters) raise `ValueError` at the same point.

---

## CLI

The package ships a CLI as the `gtm-linear` console command. It wraps
`LinearWorkflow`, so typed reads and opt-in writes are available without writing
Python:

```bash
# from a checkout
uv run gtm-linear viewer

# one-off, no local install (PyPI >= 0.3.0 ships the executable)
uvx gtm-linear issues --team ENG --limit 10

# fetch every matching issue, with useful triage filters
uvx gtm-linear issues --team ENG --all --priority High --assignee me

# or install it as a tool
uv tool install gtm-linear
gtm-linear search "onboarding"
gtm-linear search onboarding --all
```

Auth uses the SDK's `LinearSettings` resolution: `LINEAR_API_KEY` (`lin_api_...`) from the environment or a `.env` / `.env.local` file in the working directory. Endpoint overrides (`LINEAR_BASE_URL`, `LINEAR_TIMEOUT`) are honored from the real environment only — a dotenv file may supply the key, but never redirect where it is sent. Real environment variables also take precedence over dotenv for the key itself; note that a dotenv file in the current directory is trusted for auth, so run the CLI from directories you control.

| Command | Purpose |
| --- | --- |
| `gtm-linear viewer` | Auth check: print the user the API key belongs to |
| `gtm-linear teams` | List teams (key, name, id) |
| `gtm-linear issues --team ENG [--state open\|all\|NAME] [--priority VALUE] [--assignee NAME\|me] [--label NAME] [--limit N] [--all] [-v]` | List a team's issues, newest updated first (team key is case-insensitive; default state: open, limit: 25). State names, assignees, and labels match exactly; priority accepts Urgent/High/Medium/Low or 0–4. |
| `gtm-linear issue ENG-123` | Fetch one issue by identifier (any casing) or Linear UUID |
| `gtm-linear issue create --team ENG --title "..." [--description ...] [--priority 0-4] [--assignee-id UUID] [--project-id UUID] [--state-id UUID]` | Preview an issue creation; resolve the team key and show the typed mutation payload |
| `gtm-linear issue update ENG-123 [fields...]` | Preview changes to title, description, priority, assignee, project, or state; use `--clear-description`, `--clear-priority`, `--clear-assignee`, `--clear-project`, or `--clear-state` to clear nullable fields |
| `gtm-linear issue comment ENG-123 --body "..."` | Preview a Markdown comment on an issue |
| `gtm-linear search "term" [--limit N] [--all] [-v]` | Free-text issue search across the workspace (multi-word terms may be unquoted — words are joined; dash-prefixed values are searched as-is, so a mistyped flag becomes the term) |

Write commands are non-interactive and **preview by default**: target resolution may issue read queries, but no mutation is sent unless `--apply` is present. Add `--apply` to any create, update, or comment command to execute it. Creation requires a team key and title; updates require at least one field or clear flag. Assignee, project, and workflow-state options take Linear IDs. `--json` previews return an envelope with `applied: false`, operation, target, and payload; successful applied writes return the created or updated resource. If a mutation call fails, JSON mode emits an error envelope with `applied: false`, operation, target, and error, then exits 1. No write command reads stdin or prompts for confirmation.

Every command accepts `--json` for machine-readable output. `issues` and `search` default to 25 and 10 results respectively; `--limit` accepts 1–100, and `--all` cannot be combined with `--limit`. Human-readable listings say whether all matching results were fetched or more may remain. Existing bounded JSON output remains an array; successful `--all --json` emits a `results` array with `complete` and `truncated` booleans. If pagination stalls in JSON mode, the CLI emits an error envelope containing any results fetched so far, `complete: false`, `truncated: true`, and the error, then exits 1. Without JSON mode, runtime failures use a single red `error: …` line on stderr and never a traceback; a closed output pipe, as in `| head`, exits 1 without printing an error. Exit codes: `0` success, `1` runtime failure, `130` Ctrl-C, `2` usage error.

---

## Mental model

Three classes, all importable from the package root:

```text
LinearClient        # transport + auth + GraphQL execution
  ├── LinearQueries # typed read wrappers (get_issue, list_issues, search_issues, get_team, get_user)
  └── LinearMutations # typed write wrappers (issues and comments)
LinearWorkflow      # injected-key CLI facade over every typed read/write helper
```

`LinearQueries` and `LinearMutations` are **stateless facades** over a `LinearClient`. They do not own the client; they borrow it. Construct one client and pass it to both.

The low-level query and mutation methods are async-only: await each operation, or
use `async for` with an `iter_*` method. For synchronous scripts, use the
`LinearWorkflow` facade shown below.

Pydantic models validate Linear response payloads and mutation inputs internally. Strawberry's Pydantic integration exposes those validated models as the public GraphQL types and inputs.

```python
import asyncio
from gtm_linear import (
    IssueCreateInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
    PaginationOrderBy,
)

async def main() -> None:
    async with LinearClient(api_key="lin_api_xxx") as client:
        queries = LinearQueries(client)
        mutations = LinearMutations(client)

        team = await queries.get_team_by_key("ENG")
        assert team is not None
        issue_filter = {"team": {"id": {"eq": team.id}}}
        issues = await queries.list_issues_page(
            issue_filter,
            first=20,
            order_by=PaginationOrderBy.updatedAt,
        )
        created = await mutations.create_issue(
            IssueCreateInput(title="Hello", teamId=team.id, description="from agent"),
        )

asyncio.run(main())
```

### CLI and automation facade

For a command-line tool or automation that receives an API key from its own
configuration or secret manager, use `LinearWorkflow`. It owns the client and
provides matching synchronous and async methods; synchronous methods are for
normal CLI entrypoints, while `*_async` methods are for an existing event loop.

```python
from gtm_linear import IssueCreateInput, LinearWorkflow

with LinearWorkflow(injected_api_key) as linear:
    team = linear.get_team_by_key("ENG")
    assert team is not None
    open_issues = linear.list_open_team_issues(team.id)
    issue = linear.create_issue(
        IssueCreateInput(title="Investigate alert", team_id=team.id)
    )
```

`list_open_team_issues` excludes completed and canceled states and orders by most
recent update. For ad-hoc GraphQL not yet represented by the typed API, access
`linear.client.execute(...)` or use `LinearClient` directly.

---

## Public API surface

Importable from `gtm_linear`:

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `LinearClient` | class | Transport + auth + raw GraphQL execution |
| `LinearQueries` | class | Typed read helpers |
| `LinearMutations` | class | Typed write helpers |
| `LinearWorkflow` | class | Injected-key sync/async facade for CLI workflows |
| `LinearAPIError` | exception | Raised on HTTP non-200 OR GraphQL `errors` field present |
| `LinearPaginationError` | exception | Raised by `iter_*` when a connection stalls: several consecutive empty pages while `hasNextPage` stays true |
| `LinearWorkflowStateLookupError` | exception | Raised when a state-type lookup finds zero or multiple matching workflow states |
| `Issue` | model | Linear issue |
| `Attachment` | model | Issue attachment linking an external URL |
| `Comment` | model | Linear issue comment |
| `IssueRelation` | model | Relationship between two issues |
| `IssueContextComment` | model | Comment body, author(s), URL, and creation time |
| `IssueContextAttachment` | model | Attachment title, URL, source, and metadata |
| `IssueContextRelation` | model | Relation type and both issue endpoints |
| `IssueConnection` | model | Paginated issue list (`nodes`, `pageInfo`) |
| `IssueCommentConnection`, `IssueAttachmentConnection` | model | Paginated issue context collections |
| `IssueRelationConnection`, `IssueInverseRelationConnection` | model | Paginated outgoing and incoming issue relations |
| `IssueCreateInput` | input | `title`, `teamId`, optional `description`, `labelIds`, `priority`, `assigneeId`, `projectId`, `stateId` |
| `IssueUpdateInput` | input | Optional `title`, `description`, `labelIds`, `priority`, `assigneeId`, `projectId`, `stateId` |
| `AttachmentCreateInput` | input | Required `issueId`, `url`, `title`; optional `subtitle` and schema-supported metadata |
| `IssueRelationCreateInput` | input | Required `issueId`, `relatedIssueId`, `type` (`IssueRelationType`) |
| `IssueRelationType` | enum | Supported relation values: `blocks`, `duplicate`, `related`, `similar` |
| Issue filter mapping | `dict[str, Any]` | Linear-shaped nested issue filter passed through to GraphQL |
| `PaginationOrderBy` | enum | Supported issue connection ordering (`createdAt`, `updatedAt`) |
| `Team` | model | `id`, `name`, `key` |
| `TeamConnection` | model | Paginated teams |
| `User` | model | `id`, `name`, `email`, `active` |
| `UserConnection` | model | Paginated users |
| `Project` | model | `id`, `name`, `slug` |
| `ProjectConnection` | model | Paginated projects |
| `WorkflowState` | model | Linear workflow state (`id`, `name`, `type`, `color`, `position`) |
| `WorkflowStateConnection` | model | Paginated workflow states |
| `PageInfo` | model | `hasNextPage`, `hasPreviousPage`, `startCursor`, `endCursor` |

The public Strawberry types are backed by Pydantic models, so malformed API payloads and invalid mutation inputs fail validation before they are exposed to callers or sent to Linear.

`IssueCreateInput` and `IssueUpdateInput` are Strawberry input types backed by Pydantic models. Construct positionally or with kwargs; some static type checkers may flag the call signature — the `scripts/smoke.py` file demonstrates the working ignore pattern. Optional fields set to `None` are omitted from mutation variables.

---

## `LinearClient` reference

```python
LinearClient(api_key: str)
```

State:
- `BASE_URL = "https://api.linear.app/graphql"` (class attribute, overrideable on subclasses or by monkeypatch in tests)
- Lazily creates an `httpx.Client` (sync) and `httpx.AsyncClient` (async) on first use.
- Connection reuse: both clients persist across calls until their respective close method or context-manager exit.

### Methods

| Method | Sync/Async | Returns | Raises |
| --- | --- | --- | --- |
| `execute(query, variables=None)` | sync | `dict[str, Any]` — the `data` payload | `LinearAPIError` |
| `execute_async(query, variables=None)` | async | `dict[str, Any]` — the `data` payload | `LinearAPIError` |
| `close()` | sync | `None` | `RuntimeError` if an async client is still open |
| `aclose()` | async | `None` | — |
| `__enter__` / `__exit__` | sync ctx mgr | — | — |
| `__aenter__` / `__aexit__` | async ctx mgr | — | — |

### Error contract

`execute` / `execute_async` raise `LinearAPIError` if **either**:
1. The response JSON contains a top-level `errors` key (GraphQL-level failure), OR
2. The HTTP status is not 200, OR
3. The response is not parseable JSON / not a dict.

`LinearAPIError.errors` is a list of `GraphQLError` objects (empty for transport-level failures); `LinearAPIError.message` is a human-readable summary. Inspect `.errors` to recover structured codes — each entry exposes `.code`, `.message`, and `.extensions`.

### Return value

The methods **strip the outer `{"data": ...}` envelope** and return the inner dict. So for a query of `query { viewer { id } }`, you get back `{"viewer": {"id": "..."}}`.

### Client pitfalls

- Use `close()` for sync-only use and `await aclose()` for async-only use. `close()` raises `RuntimeError` if an async client remains open, so it cannot silently leak the async connection pool. `async with` closes both transports if they were created.
- `BASE_URL` is the **production** Linear endpoint. There is no staging URL toggle.
- The `Authorization` header is set to the raw API key string. Linear expects no `Bearer ` prefix; do not add one.

---

## `LinearQueries` reference

Coroutine methods are `async`; `iter_*` methods return async iterators and are
consumed with `async for`. All accept Linear UUIDs unless noted; issue-context
methods also accept human-readable issue identifiers.

| Method | Args | Returns | Notes |
| --- | --- | --- | --- |
| `get_issue(issue_id)` | `str` | `Issue \| None` | Returns `None` on not-found (not an error) |
| `list_issue_comments_page(issue_id, first=50, after=None, include_archived=False)` | `str`, `int`, `str \| None`, `bool` | `IssueCommentConnection` | One typed comment page, including workspace or external author |
| `iter_issue_comments(issue_id, page_size=50, limit=None, include_archived=False)` | `str`, `int`, `int \| None`, `bool` | `AsyncIterator[IssueContextComment]` | Follows every comment page automatically |
| `list_issue_attachments_page(issue_id, first=50, after=None, include_archived=False)` | `str`, `int`, `str \| None`, `bool` | `IssueAttachmentConnection` | One typed attachment page with source metadata |
| `iter_issue_attachments(issue_id, page_size=50, limit=None, include_archived=False)` | `str`, `int`, `int \| None`, `bool` | `AsyncIterator[IssueContextAttachment]` | Follows every attachment page automatically |
| `list_issue_relations_page(issue_id, first=50, after=None, include_archived=False)` | `str`, `int`, `str \| None`, `bool` | `IssueRelationConnection` | One page of outgoing relations |
| `iter_issue_relations(issue_id, page_size=50, limit=None, include_archived=False)` | `str`, `int`, `int \| None`, `bool` | `AsyncIterator[IssueContextRelation]` | Follows every outgoing relation page |
| `list_issue_inverse_relations_page(issue_id, first=50, after=None, include_archived=False)` | `str`, `int`, `str \| None`, `bool` | `IssueInverseRelationConnection` | One page of incoming relations |
| `iter_issue_inverse_relations(issue_id, page_size=50, limit=None, include_archived=False)` | `str`, `int`, `int \| None`, `bool` | `AsyncIterator[IssueContextRelation]` | Follows every incoming relation page |
| `list_issues(team_id, first=50)` | `str`, `int` | `list[Issue]` | Compatibility helper for a team's first issue page |
| `list_issues_page(filter=None, first=50, after=None, order_by=None, include_archived=False)` | `dict[str, Any] \| None`, `int`, `str \| None`, `PaginationOrderBy \| None`, `bool` | `IssueConnection` | Root issue connection with cursor pagination |
| `search_issues(term)` | `str` | `list[Issue]` | Backed by Linear's `searchIssues` GraphQL field |
| `get_team(team_id)` | `str` | `Team \| None` | UUID only; use `get_team_by_key` for `ENG`-style keys |
| `get_team_by_key(key)` | `str` | `Team \| None` | Resolves a human team key such as `ENG` |
| `get_user(user_id)` | `str` | `User \| None` | — |
| `list_workflow_states(team_id, first=50)` | `str`, `int` | `list[WorkflowState]` | Convenience helper for a team's first workflow-state page; use `iter_workflow_states` for all pages |
| `list_workflow_states_page(team_id, first=50, after=None, include_archived=False, order_by=None)` | `str`, `int`, `str \| None`, `bool`, `PaginationOrderBy \| None` | `WorkflowStateConnection` | Team-scoped workflow-state connection with cursor pagination |
| `iter_workflow_states(team_id, page_size=50, limit=None, include_archived=False, order_by=None)` | `str`, `int`, `int \| None`, `bool`, `PaginationOrderBy \| None` | `AsyncIterator[WorkflowState]` | Follows all pages automatically |
| `get_workflow_state_by_type(team_id, state_type, include_archived=False)` | `str`, `str`, `bool` | `WorkflowState` | Returns the unique state of that type; raises `LinearWorkflowStateLookupError` for zero or multiple matches |

### Team workflow states

Workflow states are queried directly from Linear's `workflowStates` connection and
filtered by team ID. `list_workflow_states` returns only the first page; use the
iterator when you want every state:

```python
async for state in queries.iter_workflow_states(team.id):
    print(state.id, state.name, state.type)
```

For an operation such as completing an issue, resolve the state by type. This
searches all pages and succeeds only when exactly one state matches:

```python
done = await queries.get_workflow_state_by_type(team.id, "completed")
await mutations.update_issue(issue.id, IssueUpdateInput(state_id=done.id))
```

If zero or multiple states have that type, the method raises
`LinearWorkflowStateLookupError`; it does not guess. Archived states are excluded
by default. Pass `include_archived=True` to include them. A type string that does
not match any state of this team raises `LinearWorkflowStateLookupError` with
`multiple=False`. API failures propagate as `LinearAPIError`; a stalled state
connection propagates as `LinearPaginationError`.

Valid state types are `triage`, `backlog`, `unstarted`, `started`, `completed`,
and `canceled`. A team may have multiple states with the same type—especially
`canceled` states such as Canceled and Duplicate—so the lookup can raise for
ambiguity even when that type is valid.

Use the page method when you need explicit cursor metadata:

```python
states = await queries.list_workflow_states_page(team.id, first=50)
for state in states.nodes:
    print(state.id, state.name, state.type)

if states.page_info.has_next_page:
    next_page = await queries.list_workflow_states_page(
        team.id,
        first=50,
        after=states.page_info.end_cursor,
    )
```

### Team key → ID and filtered issue pages

Use `get_team_by_key` to resolve a human-facing team key, then query a cursor-aware
connection. The following finds open issues in that team, ordered by their latest update:

```python
from gtm_linear import (
    PaginationOrderBy,
)

team = await queries.get_team_by_key("ENG")
assert team is not None
issue_filter = {
    "team": {"id": {"eq": team.id}},
    "state": {"type": {"nin": ["completed", "canceled"]}},
}
page_size = 100
order_by = PaginationOrderBy.updatedAt
issues = await queries.list_issues_page(
    issue_filter,
    first=page_size,
    order_by=order_by,
)
for issue in issues.nodes:
    print(issue.identifier)

if issues.page_info.has_next_page:
    next_page = await queries.list_issues_page(
        issue_filter,
        first=page_size,
        after=issues.page_info.end_cursor,
        order_by=order_by,
    )
```

### Issue shape returned by queries

`get_issue` and issue list/search methods return this projection:

```python
Issue(
    id: str,
    title: str,
    description: str | None,
    identifier: str,       # e.g. "ENG-123" — the human-readable ID
    url: str,
    priority: int | None,  # 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low (Linear convention)
    status: str | None,    # state.name flattened — e.g. "In Progress"
    assignee: User | None,
)
```

`status` is a **flattened string** (the state's `name`), not the full Linear `WorkflowState` object. If you need state ID or color, use `execute_async` directly.

### Issue comments, attachments, and relations

`get_issue` intentionally remains a lightweight projection. Use the issue-context
page methods when a caller needs associated records, or the iterators to retrieve
all pages without managing cursors. Each connection has an independent cursor;
incoming and outgoing relations are exposed separately. Archived records are
excluded by default and can be included with `include_archived=True`.

```python
comments = await queries.list_issue_comments_page("ENG-123", first=50)
if comments.page_info.has_next_page:
    next_comments = await queries.list_issue_comments_page(
        "ENG-123",
        first=50,
        after=comments.page_info.end_cursor,
    )

async for comment in queries.iter_issue_comments("ENG-123"):
    if comment.user is not None:
        author_name = comment.user.name
    elif comment.external_user is not None:
        author_name = comment.external_user.display_name
    else:
        author_name = None
    print(comment.created_at, author_name, comment.body)

async for attachment in queries.iter_issue_attachments("ENG-123"):
    print(attachment.title, attachment.url, attachment.source_type)

async for relation in queries.iter_issue_relations("ENG-123"):
    print(relation.type, relation.related_issue.identifier)

async for relation in queries.iter_issue_inverse_relations("ENG-123"):
    print(relation.type, relation.issue.identifier)
```

An issue that does not exist returns a valid empty context page. Empty connections
also retain their page metadata, and malformed response shapes continue to raise
Pydantic validation errors.

---

## `LinearMutations` reference

| Method | Args | Returns | Raises |
| --- | --- | --- | --- |
| `create_issue(input_)` | `IssueCreateInput` | `Issue` (full) | `ValueError` if API returns no issue; `LinearAPIError` on transport failure |
| `update_issue(issue_id, update)` | `str`, `IssueUpdateInput` | `Issue` (full) | `ValueError` if API returns no issue; `LinearAPIError` on transport failure |
| `delete_issue(issue_id)` | `str` | `bool` (success flag) | `LinearAPIError` on transport failure |
| `create_comment(issue_id, body)` | `str`, `str` | `Comment` (full) | `ValueError` if API returns no comment; `LinearAPIError` on transport failure |
| `create_attachment(input_)` | `AttachmentCreateInput` | `Attachment` (typed projection) | Pydantic validation error for malformed response; `LinearAPIError` on API failure |
| `create_issue_relation(input_)` | `IssueRelationCreateInput` | `IssueRelation` (both issue identifiers included) | Pydantic validation error for malformed response; `LinearAPIError` on API failure |

### Mutation pitfalls

- `IssueCreateInput` and `IssueUpdateInput` omit fields set to `None`; values such as `priority=0` and `labelIds=[]` are forwarded to Linear.
- `delete_issue` returns Linear's `success` bool. A `False` return is *not* an exception — check it explicitly if you care.
- `create_issue` and `update_issue` raise `ValueError`, not `LinearAPIError`, when the API responds 200 but with an empty `issue`. Catch both if you're wrapping.
- `create_comment` returns a typed `Comment` with `createdAt` parsed as a timezone-aware `datetime` when Linear returns an ISO-8601 timestamp.
- `create_attachment` returns Linear's existing attachment when the same URL is already linked to that issue; Linear updates that attachment rather than creating a duplicate.
- Generated attachment and relation inputs validate required IDs, URLs, and relation types before sending the request.

### Linking external work and relating issues

Use `create_attachment` to link an external resource such as a GitHub pull request.
Linear identifies an attachment by its issue and URL, updating it if that URL is
already linked. Use `create_issue_relation` for supported relationships:

```python
from gtm_linear import (
    AttachmentCreateInput,
    IssueRelationCreateInput,
    IssueRelationType,
    LinearMutations,
)

async def link_work(mutations: LinearMutations) -> None:
    attachment = await mutations.create_attachment(
        AttachmentCreateInput(
            issue_id="ENG-123",
            url="https://github.com/acme/service/pull/456",
            title="Implement service change",
            subtitle="Pull request #456",
        ),
    )
    relation = await mutations.create_issue_relation(
        IssueRelationCreateInput(
            issue_id="ENG-123",
            related_issue_id="ENG-124",
            type=IssueRelationType.blocks,
        ),
    )
```

`LinearWorkflow` exposes the same methods as `create_attachment_async` /
`create_attachment` and `create_issue_relation_async` /
`create_issue_relation` for async and synchronous workflows.

---

## Sync vs async

The low-level typed wrappers (`LinearQueries`, `LinearMutations`) are async-only.
For synchronous CLI code, prefer `LinearWorkflow`, which owns the client and runs
its async methods safely. Its synchronous methods must not be called from an active
event loop; use their `*_async` counterpart there.

To use the low-level wrappers from sync code, wrap them with `asyncio.run`:

```python
import asyncio
from gtm_linear import LinearClient, LinearQueries

async def fetch() -> None:
    async with LinearClient(api_key="...") as client:
        return await LinearQueries(client).get_issue("iss-1")

issue = asyncio.run(fetch())
```

For sync-only use, drop down to `LinearClient.execute(...)` directly.

---

## Error handling pattern

```python
from gtm_linear import LinearAPIError, LinearClient

try:
    async with LinearClient(api_key=key) as client:
        data = await client.execute_async("query { viewer { id } }")
except LinearAPIError as exc:
    # Both transport and GraphQL errors land here.
    print(exc.message)
    for err in exc.errors:  # err is a GraphQLError
        print(err.code, err.message)
```

Common Linear error codes worth branching on (found in `errors[].extensions.code`):

- `AUTHENTICATION_ERROR` — bad / missing API key
- `FORBIDDEN` — key lacks scope for the operation
- `INVALID_INPUT` — malformed mutation input
- `RATELIMITED` — back off and retry

The SDK does **not** retry on rate limits. Implement back-off at the call site.

---

## Live smoke test

Read-only by default. Use to verify auth, network, and basic schema access:

```bash
LINEAR_API_KEY=lin_api_xxx uv run python scripts/smoke.py --team-key ENG
# add --create to also create+delete a throwaway issue
```

The script exercises: `viewer` query, `get_team_by_key`, `get_team`, `list_issues_page`, `search_issues`, and optionally `create_issue` + `delete_issue`. Source: `scripts/smoke.py`.

---

## Repository layout

```text
sdk-python-linear/
├── gtm_linear/
│   ├── __init__.py           # public re-exports
│   ├── _generated/           # codegen output: Pydantic models + GraphQL documents
│   ├── _schema.py            # generated Strawberry mirror ([strawberry] extra)
│   ├── cli.py                # read-only CLI behind the gtm-linear command
│   ├── client.py             # LinearClient (httpx transport)
│   ├── exceptions.py         # LinearAPIError and friends
│   ├── models.py             # LinearModel base class
│   ├── mutations.py          # LinearMutations (async write helpers)
│   ├── pagination.py         # cursor-following paginate helpers
│   ├── queries.py            # LinearQueries (async read helpers)
│   ├── settings.py           # LinearSettings (opt-in env/dotenv config)
│   └── workflow.py           # LinearWorkflow (sync/async facade)
├── tests/                    # respx-mocked suite (incl. test_cli.py)
├── scripts/                  # codegen toolchain + smoke.py (live API)
├── operations/               # GraphQL selection sets (codegen inputs)
├── schema/                   # Linear SDL pin (codegen input)
├── pyproject.toml            # hatchling; deps, extras, console scripts
├── CHANGELOG.md              # release notes
├── ruff.toml                 # lint config (repo-local, incl. per-file ignores)
├── pyrightconfig.json        # pyright config (repo-local)
├── pyrefly.toml              # type-checker config
├── .github/workflows/        # pypi.yml (publish), pullfrog.yml (agent harness)
├── .rwx/                     # RWX pipeline — ci.yml (canonical CI) + dagger-ref-drift.yml (weekly fallback)
└── .trunk/                   # lint config (trunk.io)
```

Build backend: `hatchling`. Wheel packages: `["gtm_linear"]`.

---

## Development

```bash
uv sync                       # install deps
uv run pytest                 # run tests (respx-mocked, no network)
uv run pytest tests/test_client.py::test_execute_sync_returns_data  # single test
uv run python scripts/gen_operations.py --check   # operations/*.graphql vs _spec.toml
uv run python scripts/codegen.py --check          # generated models vs pinned schema
trunk check --all             # lint + type check (local; run uv sync first)
trunk fmt                     # autoformat
```

CI runs on RWX: `.rwx/ci.yml` runs `pytest`, the two `--check` scripts, a lean-install task (the wheel must import without the `[strawberry]` extra), and a weekly scheduled schema-drift cron (`pytest -m network`). `.rwx/dagger-ref-drift.yml` remains a weekly fallback that verifies the publisher tag/SHA pair and reports stale releases. GitHub Actions remains for the tag-triggered PyPI publish (`pypi.yml`) and the Pullfrog agent harness (`pullfrog.yml`) — see `.rwx/.migration-inventory.md` for the port inventory and flip status. Renovate updates the paired SemVer tag and immutable SHA in the PyPI workflow; its custom regex manager is intentionally the only enabled Renovate manager because Dependabot handles GitHub Actions. `trunk check` is **not** a CI gate.

Tests use `respx` to mock `httpx` — no network access required. `pytest-asyncio` is in `auto` mode, so async test functions don't need decoration.

### Conventions

- All public methods are documented with Google-style docstrings.
- Never hand-edit `gtm_linear/_generated/` or `gtm_linear/_schema.py`; regenerate with `scripts/codegen.py` (`--check` fails CI on drift). Both are lint-ignored in `.trunk/trunk.yaml`, so fixes belong in the generator.
- Transport and API failures raise the typed hierarchy in `gtm_linear/exceptions.py`.
- Input types are constructed positionally in tests and the smoke script; some checkers flag this (see `# pyright: ignore[reportCallIssue]` in `scripts/smoke.py`).
- Tests intentionally use `assert` and `_private` internals; scoped per-file ignores in `ruff.toml` cover this, so don't add per-line `noqa`s.
- No retries, no connection pooling tuning, no logging. Add at the call site.

---

## Known gaps (read before extending)

1. **Filtering coverage**: Issue filters are plain `dict[str, Any]` mappings that mirror Linear's nested filter tree. Use `execute_async` for other Linear filters.
2. **Schema coverage**: Only a focused subset of Linear resources is typed. Attachments, cycles, projects-as-containers, workflow mutations, and webhooks remain absent.
3. **Search filtering**: `search_issues` accepts only a text term. Use `list_issues_page` for mapped team/state filtering.
4. **Subscriptions**: Not supported. Linear's `subscription` API requires WebSockets — the client is HTTP-only.
5. **Status filtering**: Workflow-state query results include the state ID and type. More advanced filters still require `execute_async`.

---

## License

MIT. See `LICENSE`.
