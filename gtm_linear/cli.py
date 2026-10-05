"""Command-line interface shipped with the gtm-linear SDK.

Installed as the ``gtm-linear`` console script; from a checkout it also runs
as ``uv run gtm-linear ...``. The SDK's
typed reads and opt-in writes are wrapped as plain shell commands so common
Linear workflows never require writing Python.

Auth: ``LINEAR_API_KEY`` (``lin_api_...``) resolved from the environment or a
``.env`` / ``.env.local`` file in the working directory — the same resolution
:class:`~gtm_linear.settings.LinearSettings` performs. Endpoint overrides
(``LINEAR_BASE_URL``, ``LINEAR_TIMEOUT``) are honored from the real
environment only: a dotenv file may supply the key, but never redirect where
it is sent.

Usage:

    gtm-linear viewer
    gtm-linear teams
    gtm-linear issues --team ENG [--state open|all|NAME] [--limit 25]
    gtm-linear issues --team ENG --all --priority High --assignee me
    gtm-linear issue ENG-123
    gtm-linear issue create --team ENG --title "Investigate alert"
    gtm-linear issue update ENG-123 --priority 2 --apply
    gtm-linear issue comment ENG-123 --body "I am looking into this"
    gtm-linear search "onboarding" [--limit 10]
    gtm-linear search onboarding --all

Append ``--json`` to any command for machine-readable output. Write commands
show a preview and send no mutation unless ``--apply`` is supplied. Options are
validated before requests: ``--limit`` accepts 1-100, ``--all`` cannot be
combined with ``--limit``, and invalid values exit with a usage error (code 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import math
import os
import re
import sys
import unicodedata
from typing import TYPE_CHECKING, Annotated, Any, NoReturn, Protocol, TypeVar

import httpx
import typer
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from typer.core import TyperGroup, _click

from . import __version__
from ._generated.CreateIssue import IssueCreateInput
from ._generated.ListIssues import PaginationOrderBy
from ._generated.UpdateIssue import IssueUpdateInput
from .exceptions import GraphQLError, LinearAPIError
from .pagination import paginate
from .settings import LinearSettings
from .workflow import LinearWorkflow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from ._generated.ListIssues import ListIssuesResultIssues
    from ._generated.SearchIssues import SearchIssuesResultSearchIssues
    from ._generated.fragments import (
        CommentFields,
        IssueFields,
        IssueSearchResultFields,
    )

    # The list and search read paths return different generated projections,
    # but with identical field spellings, so the CLI treats them alike.
    IssueLike = IssueFields | IssueSearchResultFields


_NodeT = TypeVar("_NodeT")


class _PageInfoLike(Protocol):
    @property
    def has_next_page(self) -> bool: ...

    @property
    def end_cursor(self) -> str | None: ...


class _ConnectionLike(Protocol[_NodeT]):
    @property
    def nodes(self) -> list[_NodeT]: ...

    @property
    def page_info(self) -> _PageInfoLike: ...


app = typer.Typer(
    help="Read Linear issues and opt in to writes with --apply.",
    no_args_is_help=True,
    # `--version` must work without a subcommand. Normally a group fails with
    # "Missing command." before its callback body runs; invoke_without_command
    # lets the callback run instead, while no_args_is_help above still makes a
    # bare `gtm-linear` print help rather than exit silently.
    invoke_without_command=True,
    # This is a non-interactive console script: unexpected exceptions must
    # print a plain traceback, never Typer's default Rich traceback with local
    # variables — those frames hold the settings object and its API key.
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)


class _IssueCommandGroup(TyperGroup):
    """Support legacy ``issue IDENTIFIER`` alongside nested issue commands."""

    def resolve_command(
        self,
        ctx: _click.Context,
        args: list[str],
    ) -> tuple[str | None, _click.Command | None, list[str]]:
        # A Click group normally interprets the first token after ``issue`` as
        # a subcommand. Preserve the historical read syntax for Linear issue
        # identifiers and UUIDs by rewriting those invocations to a hidden
        # child command. Unknown words remain proper command usage errors.
        if args and args[0] not in self.commands:
            identifier = args[0]
            if re.fullmatch(r"[A-Za-z0-9]+-\d+", identifier) or re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                identifier,
            ):
                args = ["_read", *args]
        return super().resolve_command(ctx, args)


issue_app = typer.Typer(
    cls=_IssueCommandGroup,
    help="Read, create, update, or comment on Linear issues.",
    invoke_without_command=True,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)

# Linear's page-size ceiling for issue connections; --limit is validated to
# this range at parse time (see the option definitions below).
MAX_PAGE_SIZE = 100

# Width of the key column in the `teams` listing.
TEAM_KEY_WIDTH = 10

# Linear's numeric priority convention: 0 = no priority set. The API sends
# priorities as floats (2.0 == High), so normalize before the label lookup.
PRIORITY_LABELS = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


class State(enum.Enum):
    """Which issues a ``issues`` listing includes."""

    open = "open"
    all = "all"


# Shared option annotations: one definition per flag so its help text stays
# identical across every command that repeats it.
JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Emit JSON instead of human-readable output."),
]
ApplyOpt = Annotated[
    bool,
    typer.Option("--apply", help="Execute this mutation; otherwise show a preview."),
]
VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Also print URLs and descriptions."),
]
AllOpt = Annotated[
    bool,
    typer.Option("--all", help="Fetch every matching result across pages."),
]


def _fail(message: str) -> NoReturn:
    """Print an error to stderr and exit 1 — one style for every failure path."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


# --- helpers (logic kept separate from the thin Typer command functions) ---


class _DotenvApiKey(BaseSettings):
    """First-pass settings: the API key from env or dotenv, and nothing else.

    Endpoint fields are absent on purpose — pass 1 must not even parse dotenv
    endpoint values, so a hostile .env cannot cause a parse error in fields it
    cannot affect. The empty default turns "no key anywhere" into an empty
    secret the caller reports, instead of a ValidationError that would need
    disentangling from pass 2's.
    """

    model_config = SettingsConfigDict(
        env_prefix="LINEAR_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        # An exported-but-empty LINEAR_API_KEY (blank CI secret,
        # `export LINEAR_API_KEY=$UNSET_VAR`) must not shadow a real key in
        # dotenv — fall through to the file instead.
        env_ignore_empty=True,
        extra="ignore",
    )
    api_key: SecretStr = SecretStr("")


class _RealEnvSettings(LinearSettings):
    """Second-pass settings: endpoint fields from the real environment only.

    ``model_config`` merges over :class:`~gtm_linear.settings.LinearSettings`,
    so only ``env_file`` needs overriding — dotenv off is the trust boundary.
    The key is pinned from pass 1 via an init kwarg so it cannot be
    re-resolved.
    """

    model_config = SettingsConfigDict(env_file=None)


def _workflow() -> LinearWorkflow:
    """Build a workflow from env/dotenv auth, exiting with help on failure.

    Two passes, one trust boundary. The key comes from the environment or a
    ``.env`` / ``.env.local`` in the working directory (a developer's own
    file); the endpoint comes from the real environment only, because a
    console script runs from arbitrary directories and an untrusted checkout's
    dotenv must not be able to redirect the Authorization header to another
    host. Unrelated exceptions propagate untouched.
    """
    try:
        key = _DotenvApiKey().api_key
        endpoint = _RealEnvSettings(api_key=key)
    except ValidationError as exc:
        # Only pass 2 can get here — pass 1's lone field has a default — so
        # this is always a bad real-env LINEAR_* value, reported with its
        # field and reason rather than as missing auth.
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        _fail(f"invalid LINEAR_* settings: {field}: {first['msg']}")
    except (OSError, UnicodeDecodeError) as exc:
        # An unreadable or non-UTF-8 dotenv file (settings read it as UTF-8)
        # escapes pydantic-settings raw; keep the no-traceback contract.
        _fail(f"could not read a .env file: {_cell(str(exc))}")
    if not key.get_secret_value().strip():
        _fail(
            "no LINEAR_API_KEY found. Create a personal API key at linear.app "
            "(Settings → Security & access → API), then export it or put it in "
            "a .env / .env.local file in the working directory.",
        )
    try:
        # Validate early, where the only possible source is LINEAR_BASE_URL —
        # so the error can blame that variable precisely instead of surfacing
        # as a generic InvalidURL at request time.
        httpx.URL(endpoint.base_url)
    except httpx.InvalidURL as exc:
        _fail(f"invalid LINEAR_BASE_URL: {_cell(str(exc))}")
    try:
        # LinearClient rejects malformed keys at construction — a pasted
        # "Bearer " prefix, embedded whitespace or control characters, mojibake
        # — so surface that as one line instead of a traceback.
        return LinearWorkflow(
            key,
            base_url=endpoint.base_url,
            timeout=endpoint.timeout,
        )
    except (ValueError, TypeError) as exc:
        _fail(f"invalid LINEAR_API_KEY: {_cell(str(exc))}")


def _note_more_results(kind: str, shown: int) -> None:
    """Warn on stderr when a listing may omit matching results.

    stderr keeps the note separate so bounded `--json` stdout stays parseable.
    """
    typer.secho(
        f"note: showing the first {shown}; more {kind} may exist",
        err=True,
    )


def _issue_dict(issue: IssueLike) -> dict[str, object]:
    """Flatten one issue into the dict shape used by ``--json`` output."""
    return {
        "identifier": issue.identifier,
        "title": issue.title,
        "description": issue.description,
        "url": issue.url,
        "state": issue.state.name,
        # Non-finite floats (a drifted payload) would serialize as bare
        # NaN/Infinity — invalid JSON for strict consumers like jq — so they
        # render as unset, matching the table path's "-".
        "priority": issue.priority if math.isfinite(issue.priority) else None,
        "assignee": issue.assignee.name if issue.assignee else None,
        "id": issue.id,
    }


def _priority_label(priority: float | None) -> str:
    """Map Linear's numeric priority to its label; ``0``/``None`` means unset.

    NaN or infinity (legal float values a drifted payload could carry) render
    as unset rather than crashing the table.
    """
    if priority is None or not math.isfinite(priority):
        return "-"
    level = int(priority)
    if level == 0:
        return "-"
    return PRIORITY_LABELS.get(level, str(level))


def _looks_like_not_found(error: GraphQLError) -> bool:
    """Best-effort issue-not-found detection for Linear's GraphQL errors.

    Linear sends no dedicated code (``INPUT_ERROR`` is shared with genuine
    input errors), so match its wording — the message names the Issue entity
    specifically, so a not-found for some other referenced entity (a team, a
    user) falls through to main()'s generic error line instead.
    """
    if "entity not found: issue" in error.message.lower():
        return True
    presentable = error.extensions.get("userPresentableMessage")
    return (
        isinstance(presentable, str)
        and "could not find referenced issue" in presentable.lower()
    )


def _normalize_team_key(value: str) -> str:
    """Validate and normalize a ``--team`` value: trimmed, uppercased, non-empty.

    Runs as an option callback, so an empty key fails at parse time (exit 2)
    instead of costing a round trip and a confusing "no Linear team with key
    ''" error.
    """
    normalized = value.strip().upper()
    if not normalized:
        error_msg = "team key must not be empty"
        raise typer.BadParameter(error_msg)
    return normalized


def _non_empty_filter(value: str | None, option: str) -> str | None:
    """Trim a free-form filter value and reject empty values as usage errors."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        error_msg = f"{option} must not be empty"
        raise typer.BadParameter(error_msg, param_hint=option)
    return normalized


def _parse_priority(value: str | None) -> int | None:
    """Accept Linear priority labels or their numeric values."""
    if value is None:
        return None
    normalized = value.strip().casefold()
    levels = {
        "urgent": 1,
        "high": 2,
        "medium": 3,
        "low": 4,
    }
    if normalized in levels:
        return levels[normalized]
    if normalized in {"0", "1", "2", "3", "4"}:
        return int(normalized)
    error_msg = "priority must be Urgent, High, Medium, Low, or a number from 0 to 4"
    raise typer.BadParameter(error_msg, param_hint="--priority")


def _normalize_state(value: str) -> str:
    """Normalize the built-in state choices and validate non-empty names."""
    normalized = value.strip()
    if not normalized:
        error_msg = "--state must not be empty"
        raise typer.BadParameter(error_msg, param_hint="--state")
    if normalized.casefold() in {"open", "all"}:
        return normalized.casefold()
    return normalized


async def _collect_paginated(
    fetch: Callable[[str | None], Awaitable[_ConnectionLike[_NodeT]]],
    *,
    limit: int | None = None,
) -> tuple[list[_NodeT], bool]:
    """Collect typed pages through the SDK paginator and report terminal state.

    ``paginate`` owns cursor advancement and stall safeguards. Retaining the
    final page's ``has_next_page`` also lets the CLI distinguish a normal end
    from the paginator's safe stop on an unusable missing/repeated cursor.
    """
    last_page: _ConnectionLike[_NodeT] | None = None
    received = 0

    async def tracked_fetch(cursor: str | None) -> _ConnectionLike[_NodeT]:
        nonlocal last_page, received
        last_page = await fetch(cursor)
        received += len(last_page.nodes)
        return last_page

    items = [item async for item in paginate(tracked_fetch, limit=limit)]
    complete = last_page is None or (
        not last_page.page_info.has_next_page and received == len(items)
    )
    return items, complete


def _collect_paginated_sync(
    linear: LinearWorkflow,
    fetch: Callable[[str | None], Awaitable[_ConnectionLike[_NodeT]]],
    *,
    limit: int | None = None,
) -> tuple[list[_NodeT], bool]:
    """Run the collector and close the workflow's async transport afterward."""

    async def collect_and_close() -> tuple[list[_NodeT], bool]:
        try:
            return await _collect_paginated(fetch, limit=limit)
        finally:
            await linear.client.aclose()

    return asyncio.run(collect_and_close())


def _build_team_issue_filter(
    linear: LinearWorkflow,
    team_id: str,
    team_key: str,
    *,
    state: str,
    priority: int | None,
    assignee: str | None,
    label: str | None,
) -> dict[str, Any]:
    """Build and validate a Linear IssueFilter for the CLI's team listing."""
    issue_filter: dict[str, Any] = {"team": {"id": {"eq": team_id}}}
    if state == State.open.value:
        issue_filter["state"] = {"type": {"nin": ["completed", "canceled"]}}
    elif state != State.all.value:
        state_names = {
            workflow_state.name
            for workflow_state in linear.iter_workflow_states(
                team_id,
                page_size=MAX_PAGE_SIZE,
            )
        }
        if state not in state_names:
            error_msg = f"unknown workflow state {state!r} for team {team_key}"
            raise typer.BadParameter(error_msg, param_hint="--state")
        issue_filter["state"] = {"name": {"eq": state}}
    if priority is not None:
        issue_filter["priority"] = {"eq": priority}
    if assignee is not None:
        if assignee.casefold() == "me":
            issue_filter["assignee"] = {"isMe": {"eq": True}}
        else:
            issue_filter["assignee"] = {"displayName": {"eq": assignee}}
    if label is not None:
        issue_filter["labels"] = {"some": {"name": {"eq": label}}}
    return issue_filter


def _read_team_issue_results(
    linear: LinearWorkflow,
    issue_filter: dict[str, Any],
    *,
    fetch_all: bool,
    limit: int | None,
) -> tuple[Sequence[IssueLike], bool]:
    page_size = MAX_PAGE_SIZE if fetch_all else (limit if limit is not None else 25)

    async def fetch_page(cursor: str | None) -> ListIssuesResultIssues:
        return await linear.list_issues_page_async(
            issue_filter,
            first=page_size,
            after=cursor,
            order_by=PaginationOrderBy.updatedAt,
        )

    return _collect_paginated_sync(
        linear,
        fetch_page,
        limit=None if fetch_all else page_size,
    )


def _read_search_results(
    linear: LinearWorkflow,
    term: str,
    *,
    fetch_all: bool,
    limit: int | None,
) -> tuple[Sequence[IssueLike], bool]:
    page_size = MAX_PAGE_SIZE if fetch_all else (limit if limit is not None else 10)

    async def fetch_page(cursor: str | None) -> SearchIssuesResultSearchIssues:
        return await linear.search_issues_async(
            term,
            first=page_size,
            after=cursor,
        )

    return _collect_paginated_sync(
        linear,
        fetch_page,
        limit=None if fetch_all else page_size,
    )


# Terminal escape sequences and C0/C1 control characters (except tab and
# newline). Linear titles, descriptions, and names are written by other
# workspace members, and escape sequences among them (ESC is \x1b) could clear
# the screen, spoof rows, or rewrite the terminal title — strip whole
# sequences first so their parameters do not survive as junk text (an OSC body
# excludes both its terminators, so it cannot run past its own end), then any
# remaining lone control characters, carriage return included.
_ANSI_SEQUENCES = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])",
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# ZWJ (\u200d) and ZWNJ (\u200c) are deliberately kept: both are zero-width
# but carry meaning — stripping ZWJ would break ZWJ emoji sequences into
# their (wider) parts, and stripping ZWNJ would silently join words in
# Persian, Kurdish, and several Indic scripts. Neither carries terminal
# control semantics.
_KEPT_FORMAT_CHARS = frozenset("\u200c\u200d")


def _clean(value: str | None) -> str:
    r"""Strip escape sequences, control characters, and invisible format chars.

    Everything else in Unicode's Cf category is invisible but can smuggle or
    visually reorder text — bidi overrides and isolates, the Arabic Letter
    Mark, soft hyphen, word joiners, the BOM, the tag block. They are
    filtered by category in code (rather than a regex range) so every current
    and future Cf code point is covered, matching the Cf-is-zero-width rule
    in ``_display_width``. ``\n`` and ``\t`` are kept; ZWJ/ZWNJ excepted above.
    """
    if value is None:
        return ""
    without_sequences = _ANSI_SEQUENCES.sub("", value)
    without_controls = _CONTROL_CHARS.sub("", without_sequences)
    return "".join(
        char
        for char in without_controls
        if char in _KEPT_FORMAT_CHARS or unicodedata.category(char) != "Cf"
    )


def _cell(value: str) -> str:
    """One table cell: control-free and single-line, so one issue cannot break the layout."""
    return " ".join(_clean(value).split())


def _display_width(value: str) -> int:
    """Display columns a cell occupies, for table alignment.

    ``len()`` counts code points, so CJK text and emoji (East-Asian Wide or
    Fullwidth — 2 columns) and non-spacing marks (0 columns) would misalign
    every column after them. Zero width is decided by Unicode category rather
    than combining class: marks like VS16 (\ufe0f) and several Indic vowel
    signs have combining class 0 but still occupy no column, and format
    characters (ZWJ, the kept-but-invisible joiner) are zero-width too. A
    narrow base followed by VS16 requests emoji presentation, which terminals
    render at double width, so the pair counts as 2.

    Best-effort and terminal-dependent: a ZWJ-joined run counts as the sum of
    its parts, which matches terminals that do not ligate it into a single
    glyph; terminals that do will render it narrower. Perfect clustering needs
    grapheme-segmentation data the standard library does not carry.
    """
    columns = 0
    index = 0
    while index < len(value):
        char = value[index]
        if (
            index + 1 < len(value)
            and value[index + 1] == "\ufe0f"
            and unicodedata.east_asian_width(char) not in ("W", "F")
            and unicodedata.category(char) not in {"Mn", "Me", "Cf"}
        ):
            # A narrow, visible base + VS16 requests emoji presentation,
            # which terminals render at double width. An already-wide base
            # keeps its own 2 (no double count) and a zero-width base its 0 —
            # for those, VS16 falls through and counts as the mark it is.
            columns += 2
            index += 2
            continue
        if unicodedata.east_asian_width(char) in ("W", "F"):
            columns += 2
        elif unicodedata.category(char) not in {"Mn", "Me", "Cf"}:
            columns += 1
        index += 1
    return columns


def _print_issues(
    issues: Sequence[IssueLike],
    *,
    as_json: bool,
    verbose: bool = False,
    all_results: bool = False,
    complete: bool = True,
    kind: str = "issues",
) -> None:
    """Render issues and, when requested, explicit pagination metadata."""
    if as_json:
        results = [_issue_dict(i) for i in issues]
        if all_results:
            typer.echo(
                json.dumps(
                    {
                        "results": results,
                        "complete": complete,
                        "truncated": not complete,
                    },
                    indent=2,
                ),
            )
        else:
            typer.echo(json.dumps(results, indent=2))
        return

    if not issues:
        typer.echo("no issues found")
        if complete:
            typer.echo(f"complete: all matching {kind} fetched")
        else:
            typer.echo(f"incomplete: more {kind} may remain")
        return

    columns = ("IDENTIFIER", "STATE", "PRIORITY", "ASSIGNEE", "TITLE")
    rows = [
        (
            _cell(i.identifier),
            _cell(i.state.name),
            _priority_label(i.priority),
            _cell(i.assignee.name) if i.assignee else "-",
            _cell(i.title),
        )
        for i in issues
    ]
    # rows is non-empty (guarded above), so every column has at least one cell.
    # Widths are display columns, not code points, so CJK and emoji titles
    # keep the columns after them aligned.
    widths = [
        max(_display_width(header), *(_display_width(row[n]) for row in rows))
        for n, header in enumerate(columns)
    ]

    def _row(cells: Sequence[str]) -> str:
        return " ".join(
            cell + " " * (widths[n] - _display_width(cell))
            for n, cell in enumerate(cells)
        ).rstrip()

    typer.echo(_row(columns))
    typer.echo(" ".join("-" * w for w in widths).rstrip())
    for row in rows:
        typer.echo(_row(row))
    if verbose:
        for issue in issues:
            # _cell flattens to one line, so an identifier or URL carrying a
            # newline cannot forge extra rows in the verbose block.
            typer.echo(f"\n{_cell(issue.identifier)} {_cell(issue.url)}")
            typer.echo(" description:")
            # Indent every line, like the `issue` command, so a multi-line
            # description cannot inject text that impersonates a header or a
            # table row.
            for line in (_clean(issue.description) or "-").splitlines():
                typer.echo(f"   {line}")
    if complete:
        typer.echo(f"complete: all matching {kind} fetched")
    else:
        typer.echo(f"incomplete: more {kind} may remain")


# --- commands ---
#
# Parameters use Typer's Annotated style with keyword-only booleans: it reads
# better than `typer.Option(False, ...)` defaults and keeps the
# boolean-positional-argument lint family (FBT) quiet.


@app.callback()
def _root(
    *,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            is_eager=True,
            help="Print the installed gtm-linear version and exit.",
        ),
    ] = False,
) -> None:
    """Read Linear issues or opt in to writes with --apply."""
    if version:
        typer.echo(f"gtm-linear {__version__}")
        raise typer.Exit


@app.command()
def viewer(
    *,
    as_json: JsonOpt = False,
) -> None:
    """Auth check: print the user the API key belongs to."""
    with _workflow() as linear:
        user = linear.get_viewer()
    if as_json:
        typer.echo(
            json.dumps(
                {"id": user.id, "name": user.name, "email": user.email},
                indent=2,
            ),
        )
    else:
        typer.echo(
            f"{_cell(user.name)} <{_cell(user.email)}> (id: {_cell(user.id)})",
        )


@app.command()
def teams(
    *,
    as_json: JsonOpt = False,
) -> None:
    """List teams (key, name, id)."""
    # No typed wrapper for the teams connection exists yet, so this command
    # uses the documented raw-GraphQL escape hatch on the workflow's client.
    with _workflow() as linear:
        data = linear.client.execute(
            f"query {{ teams(first: {MAX_PAGE_SIZE}) "
            f"{{ nodes {{ id key name }} pageInfo {{ hasNextPage }} }} }}",
        )
    # Raw-GraphQL responses are unvalidated dicts; guard the shape so a weird
    # response (missing/null teams, node without string id/key/name) fails
    # cleanly instead of raising a KeyError/AttributeError/TypeError — the
    # sanitizer chokes on non-string fields. The first guard also narrows
    # teams_data to a dict for every use below.
    teams_data = data.get("teams")
    if not isinstance(teams_data, dict):
        _fail("unexpected teams response from Linear")
    nodes = teams_data.get("nodes")

    def _well_formed(team: object) -> bool:
        return isinstance(team, dict) and all(
            isinstance(team.get(field), str) for field in ("id", "key", "name")
        )

    if not isinstance(nodes, list) or not all(_well_formed(team) for team in nodes):
        _fail("unexpected teams response from Linear")
    page_info = teams_data.get("pageInfo")
    # Like issues/search, key the truncation note on pageInfo rather than
    # guessing from a full page (exactly 100 teams would otherwise cry wolf).
    if isinstance(page_info, dict) and page_info.get("hasNextPage") is True:
        _note_more_results("teams", MAX_PAGE_SIZE)
    if as_json:
        typer.echo(json.dumps(nodes, indent=2))
        return
    if not nodes:
        typer.echo("no teams found")
        return
    for team in nodes:
        # Pad the key column by display width (like the issues table), so a
        # CJK key cannot skew the name column; _cell flattens each field to
        # one line so newlines cannot forge rows.
        key = _cell(team["key"])
        key += " " * max(0, TEAM_KEY_WIDTH - _display_width(key))
        typer.echo(f"{key} {_cell(team['name'])} (id: {_cell(team['id'])})")


@app.command()
def issues(
    *,
    team: Annotated[
        str,
        typer.Option(
            callback=_normalize_team_key,
            help="Team key, e.g. ENG (any casing).",
        ),
    ],
    state: Annotated[
        str,
        typer.Option(
            help="State: open, all, or an exact workflow-state name (default: open).",
        ),
    ] = State.open.value,
    priority: Annotated[
        str | None,
        typer.Option(help="Priority: Urgent, High, Medium, Low, or 0-4."),
    ] = None,
    assignee: Annotated[
        str | None,
        typer.Option(help="Exact assignee display name, or 'me'."),
    ] = None,
    label: Annotated[
        str | None,
        typer.Option(help="Exact issue label name."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=MAX_PAGE_SIZE,
            help=f"Max issues to fetch (1-{MAX_PAGE_SIZE}, default 25).",
        ),
    ] = None,
    fetch_all: AllOpt = False,
    verbose: VerboseOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """List a team's issues, newest updated first."""
    if fetch_all and limit is not None:
        error_msg = "--all cannot be combined with --limit"
        raise typer.BadParameter(error_msg, param_hint="--limit")
    state_value = _normalize_state(state)
    priority_value = _parse_priority(priority)
    assignee_value = _non_empty_filter(assignee, "--assignee")
    label_value = _non_empty_filter(label, "--label")

    # The --team option callback already trimmed, uppercased, and confirmed
    # the key is non-empty (mirroring the `issue` command's identifier
    # normalization).
    with _workflow() as linear:
        resolved = linear.get_team_by_key(team)
        if resolved is None:
            _fail(f"no Linear team with key {team!r}")
        issue_filter = _build_team_issue_filter(
            linear,
            resolved.id,
            team,
            state=state_value,
            priority=priority_value,
            assignee=assignee_value,
            label=label_value,
        )
        issues_found, complete = _read_team_issue_results(
            linear,
            issue_filter,
            fetch_all=fetch_all,
            limit=limit,
        )
    if not complete:
        _note_more_results("issues", len(issues_found))
    _print_issues(
        issues_found,
        as_json=as_json,
        verbose=verbose,
        all_results=fetch_all,
        complete=complete,
    )


def _normalize_issue_identifier(identifier: str) -> str:
    """Normalize human issue identifiers while leaving UUIDs unchanged."""
    if re.fullmatch(r"[A-Za-z0-9]+-\d+", identifier):
        return identifier.upper()
    return identifier


def _resolve_issue(linear: LinearWorkflow, identifier: str) -> IssueFields:
    """Resolve an issue reference or report a clean CLI not-found error."""
    normalized = _normalize_issue_identifier(identifier)
    try:
        match = linear.get_issue(normalized)
    except LinearAPIError as exc:
        if any(_looks_like_not_found(error) for error in exc.errors):
            _fail(f"no issue found with identifier {normalized}")
        raise
    if match is None:
        _fail(f"no issue found with identifier {normalized}")
    return match


def _validate_text(value: str | None, option: str) -> str | None:
    """Reject explicitly supplied blank text without changing its contents."""
    if value is not None and not value.strip():
        message = f"{option} must not be empty"
        raise typer.BadParameter(message, param_hint=option)
    return value


def _render_preview(
    operation: str,
    target: dict[str, str],
    payload: dict[str, Any],
    *,
    as_json: bool,
) -> None:
    """Show a mutation preview without sending a mutation request."""
    preview = {
        "applied": False,
        "operation": operation,
        "target": target,
        "payload": payload,
    }
    if as_json:
        typer.echo(json.dumps(preview, indent=2))
        return
    typer.echo("DRY RUN: no mutation sent (use --apply to execute)")
    typer.echo(f"operation: {operation}")
    typer.echo(f"target: {json.dumps(target, indent=2)}")
    typer.echo(f"payload: {json.dumps(payload, indent=2)}")


def _render_issue_result(issue: IssueFields, *, as_json: bool, verb: str) -> None:
    """Render a mutation's returned issue using the established CLI shape."""
    if as_json:
        typer.echo(json.dumps(_issue_dict(issue), indent=2))
        return
    typer.echo(f"{verb} {_cell(issue.identifier)}: {_cell(issue.title)}")
    typer.echo(f" url: {_cell(issue.url)}")
    typer.echo(f" id: {_cell(issue.id)}")


def _render_comment_result(
    comment: CommentFields,
    issue_identifier: str,
    *,
    as_json: bool,
) -> None:
    """Render a created comment as JSON or concise terminal output."""
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "id": comment.id,
                    "body": comment.body,
                    "url": comment.url,
                    "createdAt": comment.created_at.isoformat(),
                },
                indent=2,
            ),
        )
        return
    typer.echo(
        f"Comment added to {_cell(issue_identifier)} "
        f"(id: {_cell(comment.id)}, url: {_cell(comment.url)})",
    )


@issue_app.command(name="_read", hidden=True)
def _read(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123."),
    ],
    *,
    as_json: JsonOpt = False,
) -> None:
    """Fetch one issue by its identifier or Linear UUID."""
    identifier = _normalize_issue_identifier(identifier)
    with _workflow() as linear:
        match = _resolve_issue(linear, identifier)
    if as_json:
        typer.echo(json.dumps(_issue_dict(match), indent=2))
        return
    typer.echo(f"{_cell(match.identifier)}: {_cell(match.title)}")
    typer.echo(f" url: {_cell(match.url)}")
    typer.echo(f" state: {_cell(match.state.name)}")
    typer.echo(f" priority: {_priority_label(match.priority)}")
    typer.echo(f" assignee: {_cell(match.assignee.name) if match.assignee else '-'}")
    typer.echo(f" id: {_cell(match.id)}")
    if match.description:
        typer.echo(" description:")
        for line in _clean(match.description).splitlines():
            typer.echo(f"   {line}")


@issue_app.command()
def create(
    *,
    team: Annotated[
        str,
        typer.Option(
            callback=_normalize_team_key,
            help="Team key, e.g. ENG (any casing).",
        ),
    ],
    title: Annotated[str, typer.Option(help="New issue title.")],
    description: Annotated[
        str | None,
        typer.Option(help="Issue description in Markdown."),
    ] = None,
    priority: Annotated[
        int | None,
        typer.Option(min=0, max=4, help="Priority 0-4; 0 means no priority."),
    ] = None,
    assignee_id: Annotated[
        str | None,
        typer.Option("--assignee-id", help="Linear user UUID."),
    ] = None,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Linear project UUID."),
    ] = None,
    state_id: Annotated[
        str | None,
        typer.Option("--state-id", help="Linear workflow-state UUID."),
    ] = None,
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Create an issue; preview by default, execute with --apply."""
    title = _validate_text(title, "--title") or ""
    description = _validate_text(description, "--description")
    assignee_id = _validate_text(assignee_id, "--assignee-id")
    project_id = _validate_text(project_id, "--project-id")
    state_id = _validate_text(state_id, "--state-id")
    input_values: dict[str, Any] = {"title": title}
    for name, value in (
        ("description", description),
        ("priority", priority),
        ("assignee_id", assignee_id),
        ("project_id", project_id),
        ("state_id", state_id),
    ):
        if value is not None:
            input_values[name] = value

    with _workflow() as linear:
        resolved_team = linear.get_team_by_key(team)
        if resolved_team is None:
            _fail(f"no Linear team with key {team!r}")
        issue_input = IssueCreateInput(team_id=resolved_team.id, **input_values)
        payload = issue_input.model_dump(
            mode="json",
            by_alias=True,
            exclude_unset=True,
        )
        target = {
            "teamKey": resolved_team.key,
            "teamName": resolved_team.name,
            "teamId": resolved_team.id,
        }
        if not apply:
            _render_preview("issue.create", target, payload, as_json=as_json)
            return
        try:
            created = linear.create_issue(issue_input)
        except ValueError as exc:
            _fail(f"Linear did not return the created issue: {_cell(str(exc))}")
    _render_issue_result(created, as_json=as_json, verb="Created")


@issue_app.command()
def update(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123, or Linear UUID."),
    ],
    *,
    title: Annotated[str | None, typer.Option(help="Replace the issue title.")] = None,
    description: Annotated[
        str | None,
        typer.Option(help="Replace the Markdown description."),
    ] = None,
    clear_description: Annotated[
        bool,
        typer.Option("--clear-description", help="Clear the description."),
    ] = False,
    priority: Annotated[
        int | None,
        typer.Option(min=0, max=4, help="Priority 0-4."),
    ] = None,
    clear_priority: Annotated[
        bool,
        typer.Option("--clear-priority", help="Clear the priority."),
    ] = False,
    assignee_id: Annotated[
        str | None,
        typer.Option("--assignee-id", help="Set the Linear user UUID."),
    ] = None,
    clear_assignee: Annotated[
        bool,
        typer.Option("--clear-assignee", help="Clear the assignee."),
    ] = False,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Set the Linear project UUID."),
    ] = None,
    clear_project: Annotated[
        bool,
        typer.Option("--clear-project", help="Clear the project."),
    ] = False,
    state_id: Annotated[
        str | None,
        typer.Option("--state-id", help="Set the workflow-state UUID."),
    ] = None,
    clear_state: Annotated[
        bool,
        typer.Option("--clear-state", help="Clear the workflow state."),
    ] = False,
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Update an issue; preview by default, execute with --apply."""
    title = _validate_text(title, "--title")
    description = _validate_text(description, "--description")
    assignee_id = _validate_text(assignee_id, "--assignee-id")
    project_id = _validate_text(project_id, "--project-id")
    state_id = _validate_text(state_id, "--state-id")
    pairs = (
        ("--description", description, clear_description, "--clear-description"),
        ("--priority", priority, clear_priority, "--clear-priority"),
        ("--assignee-id", assignee_id, clear_assignee, "--clear-assignee"),
        ("--project-id", project_id, clear_project, "--clear-project"),
        ("--state-id", state_id, clear_state, "--clear-state"),
    )
    for option, value, clear, clear_option in pairs:
        if value is not None and clear:
            message = f"{option} cannot be combined with {clear_option}"
            raise typer.BadParameter(
                message,
                param_hint=option,
            )
    if title is None and not any(v is not None or c for _, v, c, _ in pairs):
        message = "provide at least one update field or --clear-* option"
        raise typer.BadParameter(
            message,
            param_hint="issue update",
        )

    update_values: dict[str, Any] = {}
    for name, value, clear in (
        ("title", title, False),
        ("description", description, clear_description),
        ("priority", priority, clear_priority),
        ("assignee_id", assignee_id, clear_assignee),
        ("project_id", project_id, clear_project),
        ("state_id", state_id, clear_state),
    ):
        if clear:
            update_values[name] = None
        elif value is not None:
            update_values[name] = value

    with _workflow() as linear:
        resolved = _resolve_issue(linear, identifier)
        update_input = IssueUpdateInput(**update_values)
        payload = update_input.model_dump(
            mode="json",
            by_alias=True,
            exclude_unset=True,
        )
        target = {"identifier": resolved.identifier, "id": resolved.id}
        if not apply:
            _render_preview("issue.update", target, payload, as_json=as_json)
            return
        try:
            updated = linear.update_issue(resolved.id, update_input)
        except ValueError as exc:
            _fail(f"Linear did not return the updated issue: {_cell(str(exc))}")
    _render_issue_result(updated, as_json=as_json, verb="Updated")


@issue_app.command()
def comment(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123, or Linear UUID."),
    ],
    *,
    body: Annotated[str, typer.Option(help="Comment body in Markdown.")],
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Add a comment; preview by default, execute with --apply."""
    body = _validate_text(body, "--body") or ""
    with _workflow() as linear:
        resolved = _resolve_issue(linear, identifier)
        payload = {"issueId": resolved.id, "body": body}
        target = {"identifier": resolved.identifier, "id": resolved.id}
        if not apply:
            _render_preview("issue.comment", target, payload, as_json=as_json)
            return
        created = linear.create_comment(resolved.id, body)
    _render_comment_result(created, resolved.identifier, as_json=as_json)


app.add_typer(issue_app, name="issue")


# Unknown leading tokens are treated as the search term, so queries like
# `gtm-linear search "-foo"` work without teaching users the `--` separator,
# and unquoted multi-word terms (`search onboarding flow`) are joined rather
# than rejected. The trade-off: a mistyped flag (`--jsno`) is searched for
# literally instead of rejected — accepted, and documented in the term's help
# text.
@app.command(context_settings={"ignore_unknown_options": True})
def search(
    words: Annotated[
        list[str],
        typer.Argument(
            help="Search term; words are joined with spaces, quoting is "
            "optional, and anything dash-prefixed is treated as the term — "
            "so a mistyped flag is searched for literally.",
        ),
    ],
    *,
    limit: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=MAX_PAGE_SIZE,
            help=f"Max results (1-{MAX_PAGE_SIZE}, default 10).",
        ),
    ] = None,
    fetch_all: AllOpt = False,
    verbose: VerboseOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Free-text issue search across the workspace."""
    term = " ".join(words).strip()
    if not term:
        # Raised as a usage error so empty input exits 2, like the other
        # parse-time validations.
        error_msg = "search term must not be empty"
        raise typer.BadParameter(error_msg)
    if fetch_all and limit is not None:
        error_msg = "--all cannot be combined with --limit"
        raise typer.BadParameter(error_msg, param_hint="--limit")
    with _workflow() as linear:
        results_found, complete = _read_search_results(
            linear,
            term,
            fetch_all=fetch_all,
            limit=limit,
        )
    if not complete:
        _note_more_results("results", len(results_found))
    _print_issues(
        results_found,
        as_json=as_json,
        verbose=verbose,
        all_results=fetch_all,
        complete=complete,
        kind="results",
    )


def main() -> None:
    """Entry point for the ``gtm-linear`` console script.

    Runs the Typer app, mapping failures to a single red ``error:`` line on
    stderr with exit code 1 instead of a traceback. Transport failures
    (offline, DNS, timeout) escape the SDK unwrapped, so they are caught here
    too — with Rich pretty exceptions disabled, they would otherwise reach the
    user as a raw traceback. ``raise ... from None`` drops the chained
    exception context, which would only echo the error twice.
    """
    try:
        app()
    except LinearAPIError as exc:
        # Exception text originates from the remote or the HTTP stack, so it
        # gets the same sanitizing as any other remote string, collapsed to
        # one line to honor the single-red-line contract.
        typer.secho(f"error: {_cell(str(exc))}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None
    except httpx.InvalidURL as exc:
        # A malformed URL from anywhere but LINEAR_BASE_URL (which _workflow
        # validates precisely — for example a hostile redirect target).
        typer.secho(
            f"error: invalid URL: {_cell(str(exc))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise SystemExit(1) from None
    except httpx.HTTPError as exc:
        typer.secho(
            f"error: could not reach Linear: {_cell(str(exc))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise SystemExit(1) from None
    except ValidationError as exc:
        # Schema drift: Linear's response no longer parses into the generated
        # models. Summarize rather than str(): pydantic embeds the raw input
        # values, which could echo kilobytes of workspace data (titles,
        # emails) into logs. (ValidationErrors from LinearSettings are handled
        # inside _workflow and never reach this branch.)
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        typer.secho(
            f"error: unexpected response shape from Linear: {_cell(field)}: "
            f"{_cell(str(first['msg']))} ({exc.error_count()} validation error(s))",
            fg=typer.colors.RED,
            err=True,
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        # Ctrl-C: exit 130 (128 + SIGINT), the shell convention, quietly.
        raise SystemExit(130) from None
    except BrokenPipeError:
        # A downstream consumer (`gtm-linear issues | head`) closed the pipe.
        # Exit code 1 matches both click's own EPIPE handling and Python's
        # documented broken-pipe guidance. The vendored Typer/Click standalone
        # loop already pacifies an EPIPE raised inside command execution, so
        # this branch is the fallback for one escaping that handling — or a
        # future engine version without it. Point stdout at devnull so the
        # interpreter's shutdown flush cannot raise a second BrokenPipeError,
        # then exit quietly — the convention. Redirection failures (stdout
        # already detached or replaced, as under test capture) are harmless:
        # nothing more is written either way.
        with contextlib.suppress(OSError, ValueError, AttributeError):
            devnull = os.open(os.devnull, os.O_WRONLY)
            with contextlib.suppress(OSError):
                os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        raise SystemExit(1) from None
    except UnicodeEncodeError as exc:
        # The output stream cannot encode remote text (a legacy Windows code
        # page, or PYTHONIOENCODING=ascii printing CJK/emoji). One line
        # instead of a traceback. The message is backslash-escaped because
        # str(exc) itself contains the unencodable character — printing that
        # raw would raise again on the same broken stream.
        safe = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        typer.secho(
            f"error: output stream cannot encode this text: {safe}",
            fg=typer.colors.RED,
            err=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
