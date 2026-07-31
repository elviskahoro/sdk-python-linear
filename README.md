# gtm-linear

Async-first Python SDK for the [Linear](https://linear.app) GraphQL API. Thin, typed wrapper around `httpx` with optional sync support, Strawberry-typed models, and explicit error semantics.

> **Status:** Pre-alpha (`0.0.1`). PyPI name reserved. API surface is small and stable but incomplete — fall back to raw `LinearClient.execute_async` for anything not yet wrapped.

---

## When to use this (agent triage)

| Situation | Use this SDK? |
| --- | --- |
| Read/write Linear issues from Python with typed responses | Yes |
| Need ad-hoc GraphQL escape hatch alongside typed helpers | Yes — `LinearClient.execute_async(query, variables)` |
| Building MCP-style tooling against Linear | Yes (low-level), or prefer the official Linear MCP server for higher-level intent |
| Need full coverage of Linear's GraphQL schema | **No** — only a focused subset of Linear types is wrapped today |
| Need webhooks, OAuth flow, or attachments | **No** — not implemented |
| Writing a one-off shell command | Prefer `cli-linear-guide` skill or `curl` against the GraphQL endpoint |

If you only need to *create or read a few issues* from an automation, this is the right tool. If you need broad schema coverage, drop down to `execute_async` with a hand-written query.

---

## Install

```bash
uv pip install gtm-linear        # once published
# or, in this repo:
uv sync
```

Requires Python `>=3.11`. Runtime deps: `httpx>=0.27`, `pydantic>=2.0`, `strawberry-graphql>=0.240`.

---

## Auth

Linear personal API key. Format: `lin_api_...`. Pass the raw key as the `Authorization` header value (no `Bearer ` prefix — Linear accepts the key directly).

```bash
export LINEAR_API_KEY=lin_api_xxx
```

The SDK does not read env vars on its own. Caller is responsible for passing `api_key=` to `LinearClient`.

---

## Mental model

Three classes, all importable from the package root:

```text
LinearClient        # transport + auth + GraphQL execution
  ├── LinearQueries # typed read wrappers (get_issue, list_issues, search_issues, get_team, get_user)
  └── LinearMutations # typed write wrappers (issues and comments)
```

`LinearQueries` and `LinearMutations` are **stateless facades** over a `LinearClient`. They do not own the client; they borrow it. Construct one client and pass it to both.

Pydantic models validate Linear response payloads and mutation inputs internally. Strawberry's Pydantic integration exposes those validated models as the public GraphQL types and inputs.

```python
import asyncio
from gtm_linear import LinearClient, LinearQueries, LinearMutations, IssueCreateInput

async def main() -> None:
    async with LinearClient(api_key="lin_api_xxx") as client:
        queries = LinearQueries(client)
        mutations = LinearMutations(client)

        team = await queries.get_team("team-uuid")
        issues = await queries.list_issues(team.id, first=20)
        created = await mutations.create_issue(
            IssueCreateInput(title="Hello", teamId=team.id, description="from agent"),
        )

asyncio.run(main())
```

---

## Public API surface

Importable from `gtm_linear`:

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `LinearClient` | class | Transport + auth + raw GraphQL execution |
| `LinearQueries` | class | Typed read helpers |
| `LinearMutations` | class | Typed write helpers |
| `LinearAPIError` | exception | Raised on HTTP non-200 OR GraphQL `errors` field present |
| `Issue` | model | Linear issue |
| `Comment` | model | Linear issue comment |
| `IssueConnection` | model | Paginated issue list (`nodes`, `pageInfo`) |
| `IssueCreateInput` | input | `title`, `teamId`, optional `description`, `labelIds`, `priority`, `assigneeId`, `projectId`, `stateId` |
| `IssueUpdateInput` | input | Optional `title`, `description`, `labelIds`, `priority`, `assigneeId`, `projectId`, `stateId` |
| `Team` | model | `id`, `name`, `key` |
| `TeamConnection` | model | Paginated teams |
| `User` | model | `id`, `name`, `email`, `active` |
| `UserConnection` | model | Paginated users |
| `Project` | model | `id`, `name`, `slug` |
| `ProjectConnection` | model | Paginated projects |
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

`LinearAPIError.errors` is the raw list of error dicts from Linear; `LinearAPIError.message` is a human-readable summary. Inspect `.errors` to recover structured codes.

### Return value

The methods **strip the outer `{"data": ...}` envelope** and return the inner dict. So for a query of `query { viewer { id } }`, you get back `{"viewer": {"id": "..."}}`.

### Client pitfalls

- Use `close()` for sync-only use and `await aclose()` for async-only use. `close()` raises `RuntimeError` if an async client remains open, so it cannot silently leak the async connection pool. `async with` closes both transports if they were created.
- `BASE_URL` is the **production** Linear endpoint. There is no staging URL toggle.
- The `Authorization` header is set to the raw API key string. Linear expects no `Bearer ` prefix; do not add one.

---

## `LinearQueries` reference

All methods are `async`. All accept Linear UUIDs unless noted.

| Method | Args | Returns | Notes |
| --- | --- | --- | --- |
| `get_issue(issue_id)` | `str` | `Issue \| None` | Returns `None` on not-found (not an error) |
| `list_issues(team_id, first=50)` | `str`, `int` | `list[Issue]` | Single page only — pagination not yet wrapped |
| `search_issues(term)` | `str` | `list[Issue]` | Backed by Linear's `searchIssues` GraphQL field |
| `get_team(team_id)` | `str` | `Team \| None` | UUID, not team key (`ENG`). See "Team key → ID" below |
| `get_user(user_id)` | `str` | `User \| None` | — |

### Team key → ID

`get_team` expects a UUID. To go from a human team key like `ENG`:

```python
data = await client.execute_async(
    "query($key: String!) { teams(filter: {key: {eq: $key}}) { nodes { id } } }",
    {"key": "ENG"},
)
team_id = data["teams"]["nodes"][0]["id"]
```

`scripts/smoke.py:29` has a reusable implementation (`resolve_team_id`).

### Issue shape returned by queries

Every issue method returns this projection:

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

---

## `LinearMutations` reference

| Method | Args | Returns | Raises |
| --- | --- | --- | --- |
| `create_issue(input_)` | `IssueCreateInput` | `Issue` (full) | `ValueError` if API returns no issue; `LinearAPIError` on transport failure |
| `update_issue(issue_id, update)` | `str`, `IssueUpdateInput` | `Issue` (full) | `ValueError` if API returns no issue; `LinearAPIError` on transport failure |
| `delete_issue(issue_id)` | `str` | `bool` (success flag) | `LinearAPIError` on transport failure |
| `create_comment(issue_id, body)` | `str`, `str` | `Comment` (full) | `ValueError` if API returns no comment; `LinearAPIError` on transport failure |

### Mutation pitfalls

- `IssueCreateInput` and `IssueUpdateInput` omit fields set to `None`; values such as `priority=0` and `labelIds=[]` are forwarded to Linear.
- `delete_issue` returns Linear's `success` bool. A `False` return is *not* an exception — check it explicitly if you care.
- `create_issue` and `update_issue` raise `ValueError`, not `LinearAPIError`, when the API responds 200 but with an empty `issue`. Catch both if you're wrapping.
- `create_comment` returns a typed `Comment` with `createdAt` parsed as a timezone-aware `datetime` when Linear returns an ISO-8601 timestamp.

---

## Sync vs async

The transport supports both. The typed wrappers (`LinearQueries`, `LinearMutations`) are **async-only** today. To use them from sync code, wrap with `asyncio.run`:

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
    for err in exc.errors:
        print(err.get("extensions", {}).get("code"), err.get("message"))
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

The script exercises: `viewer` query, `get_team` (with team-key → UUID resolution), `list_issues`, `search_issues`, and optionally `create_issue` + `delete_issue`. Source: `scripts/smoke.py`.

---

## Repository layout

```text
sdk-python-linear/
├── src/
│   ├── __init__.py           # public re-exports
│   ├── client.py             # LinearClient (httpx transport)
│   ├── exceptions.py         # LinearAPIError
│   ├── generated_types.py    # Strawberry-decorated models + input types
│   ├── queries.py            # LinearQueries (async read helpers)
│   └── mutations.py          # LinearMutations (async write helpers)
├── tests/
│   ├── test_client.py        # respx-mocked transport tests (sync + async)
│   ├── test_queries.py       # respx-mocked query parsing tests
│   └── test_mutations.py     # respx-mocked mutation tests
├── scripts/
│   └── smoke.py              # live API smoke test
├── pyproject.toml            # uv + hatchling; deps + dev deps + pytest config
├── pyrefly.toml              # type-checker config
└── .trunk/                   # lint config (trunk.io)
```

Build backend: `hatchling`. Wheel packages: `["gtm_linear"]`.

---

## Development

```bash
uv sync                       # install deps
uv run pytest                 # run tests (respx-mocked, no network)
uv run pytest tests/test_client.py::test_execute_sync_returns_data  # single test
trunk check --all             # lint + type check
trunk fmt                     # autoformat
```

Tests use `respx` to mock `httpx` — no network access required. `pytest-asyncio` is in `auto` mode, so async test functions don't need decoration.

### Conventions

- All public methods are documented with Google-style docstrings.
- Strawberry types in `generated_types.py` use `# type: ignore[misc]` on the decorator due to a known mypy ↔ Strawberry interaction.
- Input types are constructed positionally in tests and the smoke script; some checkers flag this (see `# pyright: ignore[reportCallIssue]` in `scripts/smoke.py`).
- No retries, no connection pooling tuning, no logging. Add at the call site.

---

## Known gaps (read before extending)

1. **Pagination**: `list_issues` returns one page. `PageInfo` is modeled but unused by wrappers. Use `execute_async` + cursors directly for multi-page traversal.
2. **Schema coverage**: Only `Issue`, `Comment`, `Team`, `User`, and `Project` are typed. Attachments, cycles, projects-as-containers, workflows, and webhooks remain absent.
3. **Filtering**: `list_issues` has no filter args. Pass a `filter:` directly via `execute_async`.
4. **Subscriptions**: Not supported. Linear's `subscription` API requires WebSockets — the client is HTTP-only.
5. **Status enum**: `status` is flattened to `state.name`. To filter by state ID, query `state { id }` via `execute_async`.

---

## License

MIT. See `LICENSE`.
