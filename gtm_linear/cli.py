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
    gtm-linear issue create --team ENG --title "Plan" --description-file plan.md
    gtm-linear issue update ENG-123 --priority 2 --apply
    gtm-linear issue update ENG-123 ENG-124 --state Done --apply
    gtm-linear issue update ENG-123 --description-file plan.md --apply
    gtm-linear issue comment ENG-123 --body "I am looking into this"
    cat note.md | gtm-linear issue comment ENG-123 --body-file - --apply
    gtm-linear search "onboarding" [--limit 10]
    gtm-linear search onboarding --all
    gtm-linear search "Evaluate" --team AI [--state "In Progress"]

Append ``--json`` to any command for machine-readable output. Write commands
show a preview and send no mutation unless ``--apply`` is supplied. Options are
validated before requests: ``--limit`` accepts 1-100, ``--all`` cannot be
combined with ``--limit``, and invalid values exit with a usage error (code 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import io
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn, Protocol, TypeVar

import httpx2 as httpx
import typer
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from typer.core import TyperGroup, _click

from . import __version__
from ._generated.CreateIssue import IssueCreateInput
from ._generated.ListIssues import PaginationOrderBy
from ._generated.UpdateIssue import IssueUpdateInput
from ._generated.fragments import PageInfoFields
from .exceptions import (
    LinearAPIError,
    LinearPaginationError,
    looks_like_not_found,
)
from .pagination import paginate
from .settings import LinearSettings
from .workflow import LinearWorkflow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from ._generated.ListIssueComments import ListIssueCommentsResultIssueComments
    from ._generated.ListIssues import ListIssuesResultIssues
    from ._generated.SearchIssues import SearchIssuesResultSearchIssues
    from ._generated.fragments import (
        CommentFields,
        IssueContextCommentFields,
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


class _RawConnectionPage:
    """A raw ``nodes``/``pageInfo`` page adapted to that connection protocol.

    The resolver lookups below run ad-hoc GraphQL instead of the generated
    operations, so their pages need a dict-backed stand-in before the SDK
    paginator can drive them.
    """

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        page_info: PageInfoFields,
    ) -> None:
        self._nodes = nodes
        self._page_info = page_info

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self._nodes

    @property
    def page_info(self) -> PageInfoFields:
        return self._page_info


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
FieldsOpt = Annotated[
    str | None,
    typer.Option(
        "--fields",
        help="With --json, comma-separated keys to emit; choices: "
        "identifier, title, description, url, state, priority, assignee, id, "
        "project (default: all).",
    ),
]
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

    ``env_ignore_empty`` mirrors pass 1's contract: an exported-but-empty
    real-env value (``export LINEAR_TIMEOUT=$UNSET_VAR`` in a CI template)
    falls through to the field default instead of being fed to the parser as
    ``""`` — so an empty ``LINEAR_TIMEOUT`` resolves to ``30.0`` rather than
    raising a ``ValidationError`` that would shadow the missing-key guidance.
    A future field where the empty string is itself meaningful would need to
    opt out of this on that field, since no current ``_RealEnvSettings`` field
    distinguishes ``""`` from unset.
    """

    model_config = SettingsConfigDict(env_file=None, env_ignore_empty=True)


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


_ISSUE_FIELDS = (
    "identifier",
    "title",
    "description",
    "url",
    "state",
    "priority",
    "assignee",
    "id",
    "project",
)


def _parse_fields(value: str | None) -> tuple[str, ...] | None:
    """Validate a ``--fields`` list against the keys ``--json`` can emit."""
    if value is None:
        return None
    requested = [part.strip() for part in value.split(",")]
    if not all(requested):
        error_msg = "fields must be a comma-separated list of non-empty names"
        raise typer.BadParameter(error_msg, param_hint="--fields")
    unknown = [name for name in requested if name not in _ISSUE_FIELDS]
    if unknown:
        error_msg = (
            f"unknown field(s): {', '.join(unknown)}; "
            f"valid fields: {', '.join(_ISSUE_FIELDS)}"
        )
        raise typer.BadParameter(error_msg, param_hint="--fields")
    return tuple(dict.fromkeys(requested))


def _issue_dict(
    issue: IssueLike,
    fields: Sequence[str] | None = None,
) -> dict[str, object]:
    """Flatten one issue into the dict shape used by ``--json`` output.

    ``fields`` limits the keys emitted; ``None`` keeps the full shape.
    """
    full = {
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
        "project": (
            {"id": issue.project.id, "name": issue.project.name}
            if issue.project
            else None
        ),
    }
    if fields is None:
        return full
    return {name: full[name] for name in fields}


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


def _normalize_optional_team_key(value: str | None) -> str | None:
    """``_normalize_team_key`` for options where ``--team`` may be omitted."""
    return None if value is None else _normalize_team_key(value)


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
) -> tuple[list[_NodeT], bool, LinearPaginationError | None]:
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

    items: list[_NodeT] = []
    pagination_error: LinearPaginationError | None = None
    try:
        async for item in paginate(tracked_fetch, limit=limit):
            items.append(item)
    except LinearPaginationError as exc:
        pagination_error = exc
    complete = pagination_error is None and (
        last_page is None
        or (not last_page.page_info.has_next_page and received == len(items))
    )
    return items, complete, pagination_error


def _collect_paginated_sync(
    linear: LinearWorkflow,
    fetch: Callable[[str | None], Awaitable[_ConnectionLike[_NodeT]]],
    *,
    limit: int | None = None,
) -> tuple[list[_NodeT], bool, LinearPaginationError | None]:
    """Run the collector and close the workflow's async transport afterward."""

    async def collect_and_close() -> tuple[
        list[_NodeT],
        bool,
        LinearPaginationError | None,
    ]:
        try:
            return await _collect_paginated(fetch, limit=limit)
        finally:
            await linear.client.aclose()

    return asyncio.run(collect_and_close())


def _require_workflow_state(
    linear: LinearWorkflow,
    team_id: str,
    team_key: str,
    state: str,
) -> None:
    """Fail with a usage error unless ``state`` names one of the team's states."""
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
        _require_workflow_state(linear, team_id, team_key, state)
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
) -> tuple[Sequence[IssueLike], bool, LinearPaginationError | None]:
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
    issue_filter: dict[str, Any] | None,
    fetch_all: bool,
    limit: int | None,
) -> tuple[Sequence[IssueLike], bool, LinearPaginationError | None]:
    page_size = MAX_PAGE_SIZE if fetch_all else (limit if limit is not None else 10)

    async def fetch_page(cursor: str | None) -> SearchIssuesResultSearchIssues:
        return await linear.search_issues_async(
            term,
            first=page_size,
            after=cursor,
            filter=issue_filter,
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


def _comment_author(comment: IssueContextCommentFields) -> str | None:
    if comment.user is not None:
        return comment.user.name
    if comment.external_user is not None:
        return comment.external_user.display_name
    return None


def _comment_dict(
    comment: IssueContextCommentFields,
    issue_identifier: str,
) -> dict[str, object]:
    """Flatten one comment into the dict shape used by ``--json`` output."""
    return {
        "id": comment.id,
        "issue": issue_identifier,
        "author": _comment_author(comment),
        "body": comment.body,
        "url": comment.url,
        "createdAt": comment.created_at.isoformat(),
    }


def _read_issue_comments(
    linear: LinearWorkflow,
    issue_id: str,
    *,
    fetch_all: bool,
    limit: int | None,
) -> tuple[Sequence[IssueContextCommentFields], bool, LinearPaginationError | None]:
    page_size = MAX_PAGE_SIZE if fetch_all else (limit if limit is not None else 25)

    async def fetch_page(
        cursor: str | None,
    ) -> ListIssueCommentsResultIssueComments:
        return await linear.list_issue_comments_page_async(
            issue_id,
            first=page_size,
            after=cursor,
        )

    found, complete, pagination_error = _collect_paginated_sync(
        linear,
        fetch_page,
        limit=None if fetch_all else page_size,
    )
    # Linear's issue.comments connection yields newest-first and offers no
    # ascending sortOrder, so the CLI's chronological contract is enforced
    # client-side — createdAt with a stable id tiebreak for same-second
    # comments. Partial results from a stalled fetch are reordered too.
    chronological = sorted(
        found,
        key=lambda comment: (comment.created_at, comment.id),
    )
    return chronological, complete, pagination_error


_COMMENT_PREVIEW_CHARS = 80


def _print_comments(
    comments: Sequence[IssueContextCommentFields],
    issue_identifier: str,
    *,
    as_json: bool,
    verbose: bool = False,
    all_results: bool = False,
    complete: bool = True,
) -> None:
    """Render comments (oldest first) with explicit pagination metadata."""
    if as_json:
        results = [_comment_dict(c, issue_identifier) for c in comments]
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

    if not comments:
        typer.echo("no comments found")
    elif verbose:
        for comment in comments:
            author = _cell(_comment_author(comment) or "-")
            created = comment.created_at.isoformat()
            typer.echo(f"\n{author} {created} {_cell(comment.url)}")
            # Indent every line so a multi-line body cannot impersonate a
            # header line.
            for line in (_clean(comment.body) or "-").splitlines():
                typer.echo(f"   {line}")
    else:
        for comment in comments:
            body = _cell(comment.body)
            if len(body) > _COMMENT_PREVIEW_CHARS:
                body = body[: _COMMENT_PREVIEW_CHARS - 3] + "..."
            author = _cell(_comment_author(comment) or "-")
            typer.echo(f"{comment.created_at.isoformat()} {author}: {body}")
    if complete:
        typer.echo("complete: all matching comments fetched")
    else:
        typer.echo("incomplete: more comments may remain")


def _print_issues(
    issues: Sequence[IssueLike],
    *,
    as_json: bool,
    verbose: bool = False,
    all_results: bool = False,
    complete: bool = True,
    kind: str = "issues",
    fields: Sequence[str] | None = None,
) -> None:
    """Render issues and, when requested, explicit pagination metadata."""
    if as_json:
        results = [_issue_dict(i, fields) for i in issues]
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
    fields: FieldsOpt = None,
) -> None:
    """List a team's issues, newest updated first."""
    if fetch_all and limit is not None:
        error_msg = "--all cannot be combined with --limit"
        raise typer.BadParameter(error_msg, param_hint="--limit")
    fields_value = _parse_fields(fields)
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
        issues_found, complete, pagination_error = _read_team_issue_results(
            linear,
            issue_filter,
            fetch_all=fetch_all,
            limit=limit,
        )
    if pagination_error is not None:
        _render_pagination_failure(
            issues_found,
            pagination_error,
            as_json=as_json,
            serialize=lambda i: _issue_dict(i, fields_value),
        )
    if not complete:
        _note_more_results("issues", len(issues_found))
    _print_issues(
        issues_found,
        as_json=as_json,
        verbose=verbose,
        all_results=fetch_all,
        complete=complete,
        fields=fields_value,
    )


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_CANDIDATE_LIMIT = 10


def _candidates(items: Sequence[str]) -> str:
    return ", ".join(_cell(item) for item in items) or "none"


def _raw_nodes(
    linear: LinearWorkflow,
    query: str,
    variables: dict[str, Any],
    field: str,
) -> list[dict[str, Any]]:
    """Run a raw lookup and guard the unvalidated response shape."""
    data = linear.client.execute(query, variables)
    connection = data.get(field)
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list) or not all(
        isinstance(n, dict) and isinstance(n.get("id"), str) for n in nodes
    ):
        _fail(f"unexpected {field} response from Linear")
    return nodes


def _raw_connection_page(data: dict[str, Any], field: str) -> _RawConnectionPage:
    """Guard one raw connection page and adapt it for the paginator."""
    connection = data.get(field)
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list) or not all(
        isinstance(n, dict) and isinstance(n.get("id"), str) for n in nodes
    ):
        _fail(f"unexpected {field} response from Linear")
    try:
        page_info = PageInfoFields.model_validate(
            connection.get("pageInfo") if isinstance(connection, dict) else None,
        )
    except ValidationError:
        _fail(f"unexpected {field} response from Linear")
    return _RawConnectionPage(nodes, page_info)


def _resolve_user(linear: LinearWorkflow, value: str, option: str) -> str:
    """Resolve `me`, a UUID, an email, or a name/display name to a user id."""
    if _UUID_RE.fullmatch(value):
        return value
    if value.casefold() == "me":
        return linear.get_viewer().id
    nodes = _raw_nodes(
        linear,
        "query CliResolveUser($filter: UserFilter) "
        f"{{ users(filter: $filter, first: {_CANDIDATE_LIMIT}) "
        "{ nodes { id name displayName email } } }",
        {
            "filter": {
                "or": [
                    {"name": {"eqIgnoreCase": value}},
                    {"displayName": {"eqIgnoreCase": value}},
                    {"email": {"eqIgnoreCase": value}},
                ],
            },
        },
        "users",
    )
    if not nodes:
        message = f"no user matching {value!r}"
        raise typer.BadParameter(message, param_hint=option)
    if len(nodes) > 1:
        names = [f"{n.get('name')} <{n.get('email')}> ({n['id']})" for n in nodes]
        message = f"ambiguous user {value!r}; candidates: {_candidates(names)}"
        raise typer.BadParameter(message, param_hint=option)
    return nodes[0]["id"]


def _resolve_workflow_state(
    linear: LinearWorkflow,
    team_id: str,
    team_key: str,
    value: str,
    option: str,
) -> str:
    """Resolve a workflow-state name (case-insensitive) or UUID to an id."""
    if _UUID_RE.fullmatch(value):
        return value
    states = list(linear.iter_workflow_states(team_id, page_size=MAX_PAGE_SIZE))
    matches = [s for s in states if s.name.casefold() == value.casefold()]
    if len(matches) == 1:
        return matches[0].id
    names = [s.name for s in states]
    if not matches:
        message = (
            f"unknown workflow state {value!r} for team {team_key}; "
            f"candidates: {_candidates(names)}"
        )
    else:
        message = (
            f"ambiguous workflow state {value!r} for team {team_key}; "
            f"candidates: {_candidates([f'{s.name} ({s.id})' for s in matches])}"
        )
    raise typer.BadParameter(message, param_hint=option)


def _resolve_project(linear: LinearWorkflow, value: str, option: str) -> str:
    """Resolve a project name (case-insensitive) or UUID to an id."""
    if _UUID_RE.fullmatch(value):
        return value
    nodes = _raw_nodes(
        linear,
        "query CliResolveProject($filter: ProjectFilter) "
        f"{{ projects(filter: $filter, first: {_CANDIDATE_LIMIT}) "
        "{ nodes { id name } } }",
        {"filter": {"name": {"eqIgnoreCase": value}}},
        "projects",
    )
    if not nodes:
        message = f"no project named {value!r}"
        raise typer.BadParameter(message, param_hint=option)
    if len(nodes) > 1:
        names = [f"{n.get('name')} ({n['id']})" for n in nodes]
        message = f"ambiguous project {value!r}; candidates: {_candidates(names)}"
        raise typer.BadParameter(message, param_hint=option)
    return nodes[0]["id"]


def _team_label_pages(linear: LinearWorkflow, team_id: str) -> list[dict[str, Any]]:
    """Fetch the team's labels plus workspace-level labels across every page.

    A single ``first``-bounded slice would silently truncate a large label
    set and report its labels as unknown, so the fetch follows cursors until
    the connection is exhausted, like every other CLI listing. Delegating to
    the SDK paginator also inherits its stall defenses: a run of nodeless
    pages with advancing cursors raises instead of looping forever.
    """

    async def fetch_page(cursor: str | None) -> _RawConnectionPage:
        data = await linear.client.execute_async(
            "query CliResolveLabel($filter: IssueLabelFilter, $after: String, "
            "$first: Int!) "
            "{ issueLabels(filter: $filter, after: $after, first: $first) "
            "{ nodes { id name } pageInfo "
            "{ hasNextPage endCursor hasPreviousPage startCursor } } }",
            {
                "filter": {
                    "or": [
                        {"team": {"id": {"eq": team_id}}},
                        {"team": {"null": True}},
                    ],
                },
                "after": cursor,
                "first": MAX_PAGE_SIZE,
            },
        )
        return _raw_connection_page(data, "issueLabels")

    labels, complete, pagination_error = _collect_paginated_sync(linear, fetch_page)
    if pagination_error is not None or not complete:
        # The paginator raises after a run of nodeless pages with advancing
        # cursors, and stops silently on a page that claims a next page but
        # offers no usable cursor. Either way resolution must not guess from
        # a partial label list, so fail closed instead of reporting unknown
        # labels.
        _fail("unexpected issueLabels response from Linear")
    return labels


def _resolve_labels(
    linear: LinearWorkflow,
    team_id: str,
    team_key: str,
    values: Sequence[str],
    option: str,
) -> tuple[list[str], list[str]]:
    """Resolve label names (case-insensitive) or UUIDs to (ids, display names)."""
    ids: list[str] = []
    names: list[str] = []
    labels: list[dict[str, Any]] | None = None
    for value in values:
        if _UUID_RE.fullmatch(value):
            label_id, name = value, value
        else:
            if labels is None:
                labels = _team_label_pages(linear, team_id)
            exact = [n for n in labels if n.get("name") == value]
            matches = exact or [
                n
                for n in labels
                if isinstance(n.get("name"), str)
                and n["name"].casefold() == value.casefold()
            ]
            if len(matches) != 1:
                available = [str(n.get("name")) for n in labels]
                if not matches:
                    message = (
                        f"unknown label {value!r} for team {team_key}; "
                        f"candidates: {_candidates(available)}"
                    )
                else:
                    dupes = [f"{n.get('name')} ({n['id']})" for n in matches]
                    message = (
                        f"ambiguous label {value!r} for team {team_key}; "
                        f"candidates: {_candidates(dupes)}"
                    )
                raise typer.BadParameter(message, param_hint=option)
            label_id, name = matches[0]["id"], str(matches[0].get("name"))
        if label_id not in ids:
            ids.append(label_id)
            names.append(name)
    return ids, names


def _issue_team(linear: LinearWorkflow, issue_id: str) -> tuple[str, str]:
    """Return (team id, team key) for an issue via a raw lookup."""
    data = linear.client.execute(
        "query CliIssueTeam($id: String!) { issue(id: $id) { team { id key } } }",
        {"id": issue_id},
    )
    issue = data.get("issue")
    team = issue.get("team") if isinstance(issue, dict) else None
    if (
        not isinstance(team, dict)
        or not isinstance(team.get("id"), str)
        or not isinstance(team.get("key"), str)
    ):
        _fail("unexpected issue response from Linear")
    return team["id"], team["key"]


def _friendly_or_id(
    friendly: str | None,
    explicit_id: str | None,
    friendly_option: str,
    id_option: str,
) -> str | None:
    """Apply the precedence rule: the -id option wins; say so on stderr."""
    if friendly is not None and explicit_id is not None:
        typer.secho(
            f"note: {id_option} takes precedence over {friendly_option}; "
            f"ignoring {friendly_option}",
            err=True,
        )
        return None
    return friendly


def _reject_clear_conflicts(
    pairs: Sequence[tuple[str, Any, bool, str]],
) -> None:
    for option, value, clear, clear_option in pairs:
        if value is not None and clear:
            message = f"{option} cannot be combined with {clear_option}"
            raise typer.BadParameter(message, param_hint=option)


def _check_update_fields(
    title: str | None,
    pairs: Sequence[tuple[str, Any, bool, str]],
) -> None:
    """Require at least one update field or --clear-* option."""
    if title is None and not any(v is not None or c for _, v, c, _ in pairs):
        message = "provide at least one update field or --clear-* option"
        raise typer.BadParameter(message, param_hint="issue update")


def _update_label_values(
    linear: LinearWorkflow,
    issue_id: str,
    labels: list[str] | None,
    *,
    clear_labels: bool,
    issue_team: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build label update fields (add-only, or clear all) plus display names.

    ``issue_team`` is the issue's ``(id, key)`` when the caller already
    resolved it — the ``--state`` branch needs the same lookup, and every
    sync facade call pays a fresh HTTP client, so sharing it removes a real
    extra round trip from ``issue update --state X --label Y``.
    """
    if clear_labels:
        return {"label_ids": []}, []
    if not labels:
        return {}, []
    team_id, team_key = (
        issue_team if issue_team is not None else _issue_team(linear, issue_id)
    )
    ids, names = _resolve_labels(linear, team_id, team_key, labels, "--label")
    return {"added_label_ids": ids}, names


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
        if any(looks_like_not_found(error) for error in exc.errors):
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


def _read_text_source(
    inline: str | None,
    inline_option: str,
    file_value: str | None,
    file_option: str,
) -> str | None:
    """Resolve an inline text option or its ``--*-file`` twin (``-`` = stdin).

    The two are mutually exclusive. File contents are returned exactly as
    stored (UTF-8, no newline translation).
    """
    if file_value is None:
        return inline
    if inline is not None:
        message = f"{inline_option} and {file_option} cannot be combined; pick one"
        raise typer.BadParameter(message, param_hint=file_option)
    try:
        if file_value == "-":
            if sys.stdin.isatty():
                message = "refusing to read from a TTY; pipe content into stdin"
                raise typer.BadParameter(message, param_hint=file_option)
            raw = sys.stdin.buffer.read()
        else:
            raw = Path(file_value).read_bytes()
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        message = "input is not valid UTF-8"
        raise typer.BadParameter(message, param_hint=file_option) from None
    except OSError as exc:
        message = f"cannot read {_cell(file_value)}: {_cell(exc.strerror or str(exc))}"
        raise typer.BadParameter(message, param_hint=file_option) from None


_PREVIEW_MAX_CHARS = 200
_PREVIEW_MAX_LINES = 15


def _summarize_long_string(value: object) -> object:
    """Collapse long strings to ``{preview, moreLines, bytes}`` for previews."""
    if not isinstance(value, str):
        return value
    lines = value.split("\n")
    if len(value) < _PREVIEW_MAX_CHARS and len(lines) <= _PREVIEW_MAX_LINES:
        return value
    head = "\n".join(lines[:_PREVIEW_MAX_LINES])[:_PREVIEW_MAX_CHARS]
    shown = head.count("\n") + 1
    return {
        "preview": head,
        "moreLines": max(len(lines) - shown, 0),
        "bytes": len(value.encode("utf-8")),
    }


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
        "payload": {k: _summarize_long_string(v) for k, v in payload.items()},
    }
    if as_json:
        typer.echo(json.dumps(preview, indent=2))
        return
    typer.echo("DRY RUN: no mutation sent (use --apply to execute)")
    typer.echo(f"operation: {operation}")
    typer.echo(f"target: {json.dumps(target, indent=2)}")
    typer.echo(f"payload: {json.dumps(preview['payload'], indent=2)}")


def _cli_error_message(exc: Exception) -> str:
    """Return a one-line, sanitized error summary without response payloads."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return (
            f"unexpected response shape from Linear: {_cell(field)}: "
            f"{_cell(str(first['msg']))} ({exc.error_count()} validation error(s))"
        )
    if isinstance(exc, httpx.InvalidURL):
        return f"invalid URL: {_cell(str(exc))}"
    if isinstance(exc, httpx.HTTPError):
        return f"could not reach Linear: {_cell(str(exc))}"
    return _cell(str(exc))


def _render_write_failure(
    operation: str,
    target: dict[str, str],
    exc: Exception,
    *,
    as_json: bool,
    error_prefix: str | None = None,
) -> NoReturn:
    """Render a failed applied write as JSON or use the human error contract."""
    error = _cli_error_message(exc)
    if error_prefix is not None:
        error = f"{error_prefix}: {error}"
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "applied": False,
                    "operation": operation,
                    "target": target,
                    "error": error,
                },
                indent=2,
            ),
        )
        raise typer.Exit(code=1)
    _fail(error)


def _render_pagination_failure(
    results: Sequence[Any],
    exc: LinearPaginationError,
    *,
    as_json: bool,
    serialize: Callable[[Any], dict[str, object]] = _issue_dict,
) -> NoReturn:
    """Keep JSON parseable and preserve partial results when pagination stalls."""
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "results": [serialize(item) for item in results],
                    "complete": False,
                    "truncated": True,
                    "error": _cli_error_message(exc),
                },
                indent=2,
            ),
        )
        raise typer.Exit(code=1)
    raise exc


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
    fields: FieldsOpt = None,
) -> None:
    """Fetch one issue by its identifier or Linear UUID."""
    fields_value = _parse_fields(fields)
    identifier = _normalize_issue_identifier(identifier)
    with _workflow() as linear:
        match = _resolve_issue(linear, identifier)
    if as_json:
        typer.echo(json.dumps(_issue_dict(match, fields_value), indent=2))
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


@app.command()
def states(
    team: Annotated[str, typer.Argument(callback=_normalize_team_key)],
    *,
    archived: Annotated[
        bool,
        typer.Option("--archived", help="Include archived workflow states."),
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """List a team's workflow states (name, type, id)."""
    with _workflow() as linear:
        resolved = linear.get_team_by_key(team)
        if resolved is None:
            _fail(f"no Linear team with key {team!r}")
        found = list(
            linear.iter_workflow_states(
                resolved.id,
                page_size=MAX_PAGE_SIZE,
                include_archived=archived,
            ),
        )
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {"name": s.name, "type": s.type, "id": s.id, "position": s.position}
                    for s in found
                ],
                indent=2,
            ),
        )
        return
    if not found:
        typer.echo("no workflow states found")
        return
    width = max(_display_width(_cell(s.name)) for s in found)
    for s in found:
        name = _cell(s.name)
        name += " " * (width - _display_width(name))
        typer.echo(f"{name}  {_cell(s.type):<10} (id: {_cell(s.id)})")


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
    description_file: Annotated[
        str | None,
        typer.Option(
            "--description-file",
            help="Read the description from this file (or - for stdin).",
        ),
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
    assignee: Annotated[
        str | None,
        typer.Option(
            "--assignee",
            help="Friendly assignee: `me`, email, name, or user UUID. --assignee-id wins if both are given.",
        ),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option(
            "--state",
            help="Friendly workflow state name (or UUID). --state-id wins if both are given.",
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Friendly project name (or UUID). --project-id wins if both are given.",
        ),
    ] = None,
    labels: Annotated[
        list[str] | None,
        typer.Option("--label", help="Label name or UUID (repeatable)."),
    ] = None,
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Create an issue; preview by default, execute with --apply."""
    title = _validate_text(title, "--title") or ""
    description = _validate_text(
        _read_text_source(
            description,
            "--description",
            description_file,
            "--description-file",
        ),
        "--description-file" if description_file is not None else "--description",
    )
    assignee_id = _validate_text(assignee_id, "--assignee-id")
    project_id = _validate_text(project_id, "--project-id")
    state_id = _validate_text(state_id, "--state-id")
    assignee = _validate_text(assignee, "--assignee")
    state = _validate_text(state, "--state")
    project = _validate_text(project, "--project")
    assignee = _friendly_or_id(assignee, assignee_id, "--assignee", "--assignee-id")
    state = _friendly_or_id(state, state_id, "--state", "--state-id")
    project = _friendly_or_id(project, project_id, "--project", "--project-id")

    with _workflow() as linear:
        resolved_team = linear.get_team_by_key(team)
        if resolved_team is None:
            _fail(f"no Linear team with key {team!r}")
        if assignee is not None:
            assignee_id = _resolve_user(linear, assignee, "--assignee")
        if project is not None:
            project_id = _resolve_project(linear, project, "--project")
        if state is not None:
            state_id = _resolve_workflow_state(
                linear,
                resolved_team.id,
                resolved_team.key,
                state,
                "--state",
            )
        label_ids: list[str] = []
        label_names: list[str] = []
        if labels:
            label_ids, label_names = _resolve_labels(
                linear,
                resolved_team.id,
                resolved_team.key,
                labels,
                "--label",
            )
        input_values: dict[str, Any] = {"title": title}
        for name, value in (
            ("description", description),
            ("priority", priority),
            ("assignee_id", assignee_id),
            ("project_id", project_id),
            ("state_id", state_id),
            ("label_ids", label_ids or None),
        ):
            if value is not None:
                input_values[name] = value
        issue_input = IssueCreateInput(team_id=resolved_team.id, **input_values)
        payload = issue_input.model_dump(
            mode="json",
            by_alias=True,
            exclude_unset=True,
        )
        target: dict[str, Any] = {
            "teamKey": resolved_team.key,
            "teamName": resolved_team.name,
            "teamId": resolved_team.id,
        }
        if label_names:
            target["labels"] = label_names
        if not apply:
            _render_preview("issue.create", target, payload, as_json=as_json)
            return
        try:
            created = linear.create_issue(issue_input)
        except (
            LinearAPIError,
            httpx.InvalidURL,
            httpx.HTTPError,
            ValidationError,
        ) as exc:
            _render_write_failure("issue.create", target, exc, as_json=as_json)
        except ValueError as exc:
            _render_write_failure(
                "issue.create",
                target,
                exc,
                as_json=as_json,
                error_prefix="Linear did not return the created issue",
            )
    _render_issue_result(created, as_json=as_json, verb="Created")


@dataclass(frozen=True)
class _UpdateFields:
    """Validated ``issue update`` options shared by every target."""

    title: str | None
    description: str | None
    priority: int | None
    assignee_id: str | None
    project_id: str | None
    state_id: str | None
    assignee: str | None
    state: str | None
    project: str | None
    labels: list[str] | None
    clear_description: bool
    clear_priority: bool
    clear_assignee: bool
    clear_project: bool
    clear_state: bool
    clear_labels: bool


def _collect_targets(identifiers: list[str] | None, ids_file: str | None) -> list[str]:
    """Merge positional identifiers with ``--ids-file`` lines, de-duplicated."""
    collected = list(identifiers or [])
    text = _read_text_source(None, "", ids_file, "--ids-file")
    if text is not None:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                collected.append(stripped)
    targets = list(dict.fromkeys(collected))
    if not targets:
        message = "provide at least one issue identifier or --ids-file"
        raise typer.BadParameter(message, param_hint="issue update")
    return targets


def _build_update(
    linear: LinearWorkflow,
    identifier: str,
    fields: _UpdateFields,
) -> tuple[IssueFields, IssueUpdateInput, dict[str, Any], dict[str, Any]]:
    """Resolve one issue and its friendly references into an update payload."""
    resolved = _resolve_issue(linear, identifier)
    assignee_id = fields.assignee_id
    project_id = fields.project_id
    state_id = fields.state_id
    if fields.assignee is not None:
        assignee_id = _resolve_user(linear, fields.assignee, "--assignee")
    if fields.project is not None:
        project_id = _resolve_project(linear, fields.project, "--project")
    # The state and label branches both need the issue's team; fetch it
    # once so `--state X --label Y` costs one lookup, not two.
    issue_team: tuple[str, str] | None = None
    if fields.state is not None or fields.labels:
        issue_team = _issue_team(linear, resolved.id)
        if fields.state is not None:
            state_id = _resolve_workflow_state(
                linear,
                issue_team[0],
                issue_team[1],
                fields.state,
                "--state",
            )
    update_values, label_names = _update_label_values(
        linear,
        resolved.id,
        fields.labels,
        clear_labels=fields.clear_labels,
        issue_team=issue_team,
    )
    for name, value, clear in (
        ("title", fields.title, False),
        ("description", fields.description, fields.clear_description),
        ("priority", fields.priority, fields.clear_priority),
        ("assignee_id", assignee_id, fields.clear_assignee),
        ("project_id", project_id, fields.clear_project),
        ("state_id", state_id, fields.clear_state),
    ):
        if clear:
            update_values[name] = None
        elif value is not None:
            update_values[name] = value
    update_input = IssueUpdateInput(**update_values)
    payload = update_input.model_dump(mode="json", by_alias=True, exclude_unset=True)
    target: dict[str, Any] = {
        "identifier": resolved.identifier,
        "id": resolved.id,
    }
    if label_names:
        target["labels"] = label_names
    return resolved, update_input, payload, target


def _update_item(
    linear: LinearWorkflow,
    identifier: str,
    fields: _UpdateFields,
    *,
    apply: bool,
) -> tuple[dict[str, Any], str]:
    """Run one batch item; return its result envelope and human line."""
    operation = "issue.update"
    target: dict[str, Any] = {"identifier": _normalize_issue_identifier(identifier)}
    label = identifier
    captured = io.StringIO()
    try:
        # Resolvers report through _fail (stderr + Exit); keep that message as
        # this item's error instead of aborting the batch.
        with contextlib.redirect_stderr(captured):
            resolved, update_input, payload, target = _build_update(
                linear,
                identifier,
                fields,
            )
        label = resolved.identifier
        if not apply:
            summary = {k: _summarize_long_string(v) for k, v in payload.items()}
            line = (
                f"target: {json.dumps(target, indent=2)}\n"
                f"payload: {json.dumps(summary, indent=2)}"
            )
            return (
                {
                    "applied": False,
                    "operation": operation,
                    "target": target,
                    "payload": summary,
                },
                line,
            )
        updated = linear.update_issue(resolved.id, update_input)
    except typer.Exit:
        error = captured.getvalue().strip().removeprefix("error: ")
        error = error or "could not resolve update"
    except (
        LinearAPIError,
        httpx.InvalidURL,
        httpx.HTTPError,
        ValidationError,
    ) as exc:
        error = _cli_error_message(exc)
    except ValueError as exc:
        error = f"Linear did not return the updated issue: {_cli_error_message(exc)}"
    else:
        return (
            {
                "applied": True,
                "operation": operation,
                "target": target,
                "result": _issue_dict(updated),
            },
            f"Updated {_cell(updated.identifier)}: {_cell(updated.title)}",
        )
    return (
        {"applied": False, "operation": operation, "target": target, "error": error},
        f"error {_cell(label)}: {_cell(error)}",
    )


def _update_many(
    targets: list[str],
    fields: _UpdateFields,
    *,
    apply: bool,
    as_json: bool,
) -> None:
    """Apply one update to several issues, reporting each item's outcome.

    A failing item is recorded and the rest still run; any failure exits 1.
    """
    operation = "issue.update"
    with _workflow() as linear:
        outcomes = [
            _update_item(linear, identifier, fields, apply=apply)
            for identifier in targets
        ]
    results = [result for result, _ in outcomes]
    failed = sum(1 for r in results if "error" in r)
    if as_json:
        envelope: dict[str, Any] = {
            "applied": apply and failed == 0,
            "operation": operation,
            "results": results,
        }
        if apply:
            envelope["succeeded"] = len(results) - failed
            envelope["failed"] = failed
        typer.echo(json.dumps(envelope, indent=2))
    else:
        if not apply:
            typer.echo("DRY RUN: no mutation sent (use --apply to execute)")
            typer.echo(f"operation: {operation}")
        for _, line in outcomes:
            typer.echo(line)
        if apply:
            typer.echo(f"{len(results) - failed} updated, {failed} failed")
    if failed:
        raise typer.Exit(code=1)


@issue_app.command()
def update(
    identifiers: Annotated[
        list[str] | None,
        typer.Argument(
            help="Issue identifier(s), e.g. ENG-123, or Linear UUID. "
            "Several apply the same update to each.",
        ),
    ] = None,
    *,
    ids_file: Annotated[
        str | None,
        typer.Option(
            "--ids-file",
            help="Read more identifiers from this file, one per line (or - for stdin).",
        ),
    ] = None,
    title: Annotated[str | None, typer.Option(help="Replace the issue title.")] = None,
    description: Annotated[
        str | None,
        typer.Option(help="Replace the Markdown description."),
    ] = None,
    description_file: Annotated[
        str | None,
        typer.Option(
            "--description-file",
            help="Read the new description from this file (or - for stdin).",
        ),
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
    assignee: Annotated[
        str | None,
        typer.Option(
            "--assignee",
            help="Friendly assignee: `me`, email, name, or user UUID. --assignee-id wins if both are given.",
        ),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option(
            "--state",
            help="Friendly workflow state name (or UUID). --state-id wins if both are given.",
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Friendly project name (or UUID). --project-id wins if both are given.",
        ),
    ] = None,
    labels: Annotated[
        list[str] | None,
        typer.Option("--label", help="Add a label by name or UUID (repeatable)."),
    ] = None,
    clear_labels: Annotated[
        bool,
        typer.Option("--clear-labels", help="Remove all labels."),
    ] = False,
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Update one or more issues; preview by default, execute with --apply."""
    targets = _collect_targets(identifiers, ids_file)
    title = _validate_text(title, "--title")
    description = _validate_text(
        _read_text_source(
            description,
            "--description",
            description_file,
            "--description-file",
        ),
        "--description-file" if description_file is not None else "--description",
    )
    assignee_id = _validate_text(assignee_id, "--assignee-id")
    project_id = _validate_text(project_id, "--project-id")
    state_id = _validate_text(state_id, "--state-id")
    assignee = _validate_text(assignee, "--assignee")
    state = _validate_text(state, "--state")
    project = _validate_text(project, "--project")
    _reject_clear_conflicts(
        (
            ("--assignee", assignee, clear_assignee, "--clear-assignee"),
            ("--state", state, clear_state, "--clear-state"),
            ("--project", project, clear_project, "--clear-project"),
            ("--label", labels, clear_labels, "--clear-labels"),
        ),
    )
    assignee = _friendly_or_id(assignee, assignee_id, "--assignee", "--assignee-id")
    state = _friendly_or_id(state, state_id, "--state", "--state-id")
    project = _friendly_or_id(project, project_id, "--project", "--project-id")
    pairs = (
        ("--description", description, clear_description, "--clear-description"),
        ("--priority", priority, clear_priority, "--clear-priority"),
        ("--assignee-id", assignee_id, clear_assignee, "--clear-assignee"),
        ("--project-id", project_id, clear_project, "--clear-project"),
        ("--state-id", state_id, clear_state, "--clear-state"),
        ("--assignee", assignee, False, ""),
        ("--state", state, False, ""),
        ("--project", project, False, ""),
        ("--label", labels, clear_labels, "--clear-labels"),
    )
    _reject_clear_conflicts(pairs)

    _check_update_fields(title, pairs)

    fields = _UpdateFields(
        title=title,
        description=description,
        priority=priority,
        assignee_id=assignee_id,
        project_id=project_id,
        state_id=state_id,
        assignee=assignee,
        state=state,
        project=project,
        labels=labels,
        clear_description=clear_description,
        clear_priority=clear_priority,
        clear_assignee=clear_assignee,
        clear_project=clear_project,
        clear_state=clear_state,
        clear_labels=clear_labels,
    )
    if len(targets) > 1:
        _update_many(targets, fields, apply=apply, as_json=as_json)
        return

    with _workflow() as linear:
        resolved, update_input, payload, target = _build_update(
            linear,
            targets[0],
            fields,
        )
        if not apply:
            _render_preview("issue.update", target, payload, as_json=as_json)
            return
        try:
            updated = linear.update_issue(resolved.id, update_input)
        except (
            LinearAPIError,
            httpx.InvalidURL,
            httpx.HTTPError,
            ValidationError,
        ) as exc:
            _render_write_failure(
                "issue.update",
                target,
                exc,
                as_json=as_json,
            )
        except ValueError as exc:
            _render_write_failure(
                "issue.update",
                target,
                exc,
                as_json=as_json,
                error_prefix="Linear did not return the updated issue",
            )
    _render_issue_result(updated, as_json=as_json, verb="Updated")


@issue_app.command()
def comment(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123, or Linear UUID."),
    ],
    *,
    body: Annotated[
        str | None,
        typer.Option(help="Comment body in Markdown."),
    ] = None,
    body_file: Annotated[
        str | None,
        typer.Option(
            "--body-file",
            help="Read the comment body from this file (or - for stdin).",
        ),
    ] = None,
    apply: ApplyOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Add a comment; preview by default, execute with --apply."""
    body = _read_text_source(body, "--body", body_file, "--body-file")
    if body is None:
        message = "provide --body or --body-file"
        raise typer.BadParameter(message, param_hint="--body")
    body = _validate_text(body, "--body-file" if body_file else "--body") or ""
    with _workflow() as linear:
        resolved = _resolve_issue(linear, identifier)
        payload = {"issueId": resolved.id, "body": body}
        target = {"identifier": resolved.identifier, "id": resolved.id}
        if not apply:
            _render_preview("issue.comment", target, payload, as_json=as_json)
            return
        try:
            created = linear.create_comment(resolved.id, body)
        except (
            LinearAPIError,
            httpx.InvalidURL,
            httpx.HTTPError,
            ValidationError,
            ValueError,
        ) as exc:
            _render_write_failure(
                "issue.comment",
                target,
                exc,
                as_json=as_json,
            )
    _render_comment_result(created, resolved.identifier, as_json=as_json)


@issue_app.command()
def comments(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123, or Linear UUID."),
    ],
    *,
    limit: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=MAX_PAGE_SIZE,
            help=(
                f"Max comments to fetch (1-{MAX_PAGE_SIZE}, default 25); "
                "windows the newest comments, shown oldest first."
            ),
        ),
    ] = None,
    fetch_all: AllOpt = False,
    verbose: VerboseOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """List an issue's comments, oldest first (read-only)."""
    if fetch_all and limit is not None:
        error_msg = "--all cannot be combined with --limit"
        raise typer.BadParameter(error_msg, param_hint="--limit")
    with _workflow() as linear:
        resolved = _resolve_issue(linear, identifier)
        found, complete, pagination_error = _read_issue_comments(
            linear,
            resolved.id,
            fetch_all=fetch_all,
            limit=limit,
        )
    if pagination_error is not None:
        _render_pagination_failure(
            found,
            pagination_error,
            as_json=as_json,
            serialize=lambda c: _comment_dict(c, resolved.identifier),
        )
    if not complete:
        _note_more_results("comments", len(found))
    _print_comments(
        found,
        resolved.identifier,
        as_json=as_json,
        verbose=verbose,
        all_results=fetch_all,
        complete=complete,
    )


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
    team: Annotated[
        str | None,
        typer.Option(
            callback=_normalize_optional_team_key,
            help="Only return issues from this team key, e.g. ENG (any casing).",
        ),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option(
            help="Only return issues in this exact workflow-state name "
            "(requires --team).",
        ),
    ] = None,
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
    fields: FieldsOpt = None,
) -> None:
    """Free-text issue search across the workspace, or one team with --team."""
    fields_value = _parse_fields(fields)
    term = " ".join(words).strip()
    if not term:
        # Raised as a usage error so empty input exits 2, like the other
        # parse-time validations.
        error_msg = "search term must not be empty"
        raise typer.BadParameter(error_msg)
    if fetch_all and limit is not None:
        error_msg = "--all cannot be combined with --limit"
        raise typer.BadParameter(error_msg, param_hint="--limit")
    state_value = _non_empty_filter(state, "--state")
    if state_value is not None and team is None:
        error_msg = "--state requires --team"
        raise typer.BadParameter(error_msg, param_hint="--state")
    with _workflow() as linear:
        issue_filter: dict[str, Any] | None = None
        if team is not None:
            resolved = linear.get_team_by_key(team)
            if resolved is None:
                _fail(f"no Linear team with key {team!r}")
            issue_filter = {"team": {"key": {"eq": team}}}
            if state_value is not None:
                _require_workflow_state(linear, resolved.id, team, state_value)
                issue_filter["state"] = {"name": {"eq": state_value}}
        results_found, complete, pagination_error = _read_search_results(
            linear,
            term,
            issue_filter=issue_filter,
            fetch_all=fetch_all,
            limit=limit,
        )
    if pagination_error is not None:
        _render_pagination_failure(
            results_found,
            pagination_error,
            as_json=as_json,
            serialize=lambda i: _issue_dict(i, fields_value),
        )
    if not complete:
        _note_more_results("results", len(results_found))
    _print_issues(
        results_found,
        fields=fields_value,
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
