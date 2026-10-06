"""Read-only command-line interface shipped with the gtm-linear SDK.

Installed as the ``gtm-linear`` console script; from a checkout it also runs
as ``uv run gtm-linear ...``. The SDK's
typed reads are wrapped as plain shell commands so fetching Linear issues never
requires writing Python — for writes, use the SDK directly
(:class:`~gtm_linear.LinearMutations`).

Auth: ``LINEAR_API_KEY`` (``lin_api_...``) resolved from the environment or a
``.env`` / ``.env.local`` file in the working directory — the same resolution
:class:`~gtm_linear.settings.LinearSettings` performs. Endpoint overrides
(``LINEAR_BASE_URL``, ``LINEAR_TIMEOUT``) are honored from the real
environment only: a dotenv file may supply the key, but never redirect where
it is sent.

Usage:

    gtm-linear viewer
    gtm-linear teams
    gtm-linear issues --team ENG [--state all] [--limit 25]
    gtm-linear issue ENG-123
    gtm-linear search "onboarding" [--limit 10]

Append ``--json`` to any command for machine-readable output. All commands are
read-only. Options are validated at parse time: ``--limit`` accepts 1-100 and
out-of-range values exit with a usage error (code 2) rather than being clamped.
"""

from __future__ import annotations

import contextlib
import enum
import json
import math
import os
import re
import sys
import unicodedata
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import httpx
import typer
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__
from ._generated.ListIssues import PaginationOrderBy
from .exceptions import GraphQLError, LinearAPIError
from .settings import LinearSettings
from .workflow import LinearWorkflow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ._generated.fragments import IssueFields, IssueSearchResultFields

    # The list and search read paths return different generated projections,
    # but with identical field spellings, so the CLI treats them alike.
    IssueLike = IssueFields | IssueSearchResultFields


app = typer.Typer(
    help="Fetch Linear issues via the gtm-linear SDK (read-only).",
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
VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Also print URLs and descriptions."),
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
    """Warn on stderr that a single-page listing stopped at the requested size.

    Parallel to the `teams` note: a full page means more results exist behind a
    cursor the command does not follow. stderr so `--json` stdout stays
    parseable.
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
) -> None:
    """Render an issue list as JSON or an aligned table."""
    if as_json:
        typer.echo(json.dumps([_issue_dict(i) for i in issues], indent=2))
        return

    if not issues:
        typer.echo("no issues found")
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
    """Fetch Linear issues via the gtm-linear SDK (read-only)."""
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
        State,
        typer.Option(help="Open (default) excludes completed and canceled issues."),
    ] = State.open,
    limit: Annotated[
        int,
        typer.Option(
            min=1,
            max=MAX_PAGE_SIZE,
            help=f"Max issues to fetch (1-{MAX_PAGE_SIZE}, default 25).",
        ),
    ] = 25,
    verbose: VerboseOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """List a team's issues, newest updated first."""
    # The --team option callback already trimmed, uppercased, and confirmed
    # the key is non-empty (mirroring the `issue` command's identifier
    # normalization).
    with _workflow() as linear:
        resolved = linear.get_team_by_key(team)
        if resolved is None:
            _fail(f"no Linear team with key {team!r}")
        # One code path for both states so page_info survives: the open-state
        # filter mirrors LinearWorkflow.list_open_team_issues (unfinished
        # states excluded), newest update first.
        issue_filter: dict[str, Any] = {"team": {"id": {"eq": resolved.id}}}
        if state is State.open:
            issue_filter["state"] = {"type": {"nin": ["completed", "canceled"]}}
        page = linear.list_issues_page(
            issue_filter,
            first=limit,
            order_by=PaginationOrderBy.updatedAt,
        )
    if page.page_info.has_next_page:
        _note_more_results("issues", limit)
    _print_issues(list(page.nodes), as_json=as_json, verbose=verbose)


@app.command()
def issue(
    identifier: Annotated[
        str,
        typer.Argument(help="Issue identifier, e.g. ENG-123."),
    ],
    *,
    as_json: JsonOpt = False,
) -> None:
    """Fetch one issue by its identifier or Linear UUID."""
    # Human identifiers are team-key + number ("ENG-123") and arrive in any
    # casing, so normalize those — but pass UUIDs through untouched, since
    # uppercasing a lowercase UUID would break the lookup.
    if re.fullmatch(r"[A-Za-z0-9]+-\d+", identifier):
        identifier = identifier.upper()
    with _workflow() as linear:
        # get_issue accepts Linear UUIDs and human identifiers alike.
        try:
            match = linear.get_issue(identifier)
        except LinearAPIError as exc:
            # Linear reports an unknown id as a GraphQL error rather than a
            # null issue; anything not matching its not-found wording (auth,
            # rate limit) propagates to main()'s error mapping, where the
            # user sees Linear's own text.
            if any(_looks_like_not_found(error) for error in exc.errors):
                _fail(f"no issue found with identifier {identifier}")
            raise
        if match is None:
            _fail(f"no issue found with identifier {identifier}")
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
        int,
        typer.Option(
            min=1,
            max=MAX_PAGE_SIZE,
            help=f"Max results (1-{MAX_PAGE_SIZE}, default 10).",
        ),
    ] = 10,
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
    with _workflow() as linear:
        result = linear.search_issues(term, first=limit)
    if result.page_info.has_next_page:
        _note_more_results("results", limit)
    _print_issues(list(result.nodes), as_json=as_json, verbose=verbose)


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
