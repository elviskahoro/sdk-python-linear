"""Tests for the shipped CLI (:mod:`gtm_linear.cli`).

The CLI is exercised end to end: ``CliRunner`` drives the Typer app while
``respx`` serves the wire payloads each underlying operation expects, so the
tests cover argument parsing, auth resolution, output rendering, and exit codes
through the real command stack rather than mocked-out command bodies.

``main()`` is the one piece CliRunner cannot drive faithfully (it wraps the app
with the ``LinearAPIError`` / transport → stderr + exit 1 mapping), so its
tests invoke it directly with ``sys.argv`` patched and capture stderr via
``capsys``.
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

from gtm_linear import (
    LinearAPIError,
    LinearPaginationError,
    LinearWorkflow,
    __version__,
)
from gtm_linear.cli import (
    _ANSI_SEQUENCES,
    _DotenvApiKey,
    _display_width,
    _parse_priority,
    _priority_label,
    app,
    main,
)
from tests.conftest import API_URL, issue_payload, page_info_payload, user_payload


def _plain(output: str) -> str:
    r"""Strip ANSI styling from rendered output.

    Under colored Rich rendering (CI sets FORCE_COLOR/GITHUB_ACTIONS), option
    names are split by escape sequences — ``'-\\x1b[0m\\x1b[1;36m-team'`` — so
    plain-text assertions on styled tokens must run against de-styled output.
    """
    return _ANSI_SEQUENCES.sub("", output)


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# Every mock-suite test runs isolated from the host: pydantic-settings matches
# env names case-insensitively, so clear every casing of LINEAR_* (a
# differently-cased export in a developer's shell could otherwise leak in),
# and run from an empty directory so no .env / .env.local can satisfy auth or
# redirect the respx-mocked endpoint. Tests then set exactly what they intend,
# via CliRunner env= or monkeypatch. Live (network-marked) tests opt out: they
# need the real environment and the repository's own dotenv files.
@pytest.fixture(autouse=True)
def _isolated_linear_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: Any,
) -> None:
    if request.node.get_closest_marker("network"):  # pragma: no cover - marker gate
        return
    for name in [n for n in os.environ if n.upper().startswith("LINEAR_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


# CliRunner env overrides os.environ for the duration of one invocation, which
# also outranks dotenv files for variables it sets.
AUTH = {"LINEAR_API_KEY": "lin_api_test"}

TEAM_PAYLOAD: dict[str, Any] = {"id": "team-1", "key": "ENG", "name": "Engineering"}


def _named(query: str, operation: str) -> bool:
    """Exact operation-name match, including mutations."""
    return re.search(rf"(?:query|mutation)\s+{operation}\b", query) is not None


def _graphql_router(
    *,
    team: dict[str, Any] | None = TEAM_PAYLOAD,
    issue_exists: bool = True,
    issues: list[dict[str, Any]] | None = None,
    workflow_states: list[dict[str, Any]] | None = None,
    more: bool = False,
) -> Callable[[httpx.Request], httpx.Response]:
    """Respx side effect serving each CLI operation by GraphQL document name.

    ``team=None`` means "no team matches the requested key" so the not-found
    path can be exercised without a second router. ``more=True`` makes the
    issue/search pages claim a next page, for truncation-note tests.
    """
    resolved_issues = [issue_payload("iss-1")] if issues is None else issues
    resolved_states = [] if workflow_states is None else workflow_states

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if _named(query, "GetViewer"):
            data: dict[str, Any] = {"viewer": user_payload()}
        elif _named(query, "GetTeamByKey"):
            data = {"teams": {"nodes": [team] if team else []}}
        elif _named(query, "GetIssue"):
            data = {
                "issue": issue_payload("iss-1") if issue_exists else None,
            }
        elif "mutation CreateIssue" in query:
            data = {
                "issueCreate": {
                    "success": True,
                    "issue": issue_payload("created-1"),
                },
            }
        elif "mutation UpdateIssue" in query:
            data = {
                "issueUpdate": {
                    "success": True,
                    "issue": issue_payload("iss-1"),
                },
            }
        elif "mutation CreateComment" in query:
            data = {
                "commentCreate": {
                    "success": True,
                    "comment": {
                        "id": "comment-1",
                        "body": "A comment",
                        "url": "https://linear.app/x/comment-1",
                        "createdAt": "2025-01-01T00:00:00Z",
                    },
                },
            }
        elif _named(query, "ListIssues"):
            data = {
                "issues": {
                    "nodes": resolved_issues,
                    "pageInfo": page_info_payload(has_next=more),
                },
            }
        elif _named(query, "ListWorkflowStates"):
            data = {
                "workflowStates": {
                    "nodes": resolved_states,
                    "pageInfo": page_info_payload(),
                },
            }
        elif _named(query, "SearchIssues"):
            data = {
                "searchIssues": {
                    "nodes": [issue_payload("iss-1")],
                    "pageInfo": page_info_payload(has_next=more),
                },
            }
        elif query.startswith("query { teams"):
            # The raw escape-hatch document the `teams` command executes.
            data = {
                "teams": {
                    "nodes": [team] if team else [],
                    "pageInfo": page_info_payload(has_next=more),
                },
            }
        else:
            raise AssertionError(f"unexpected GraphQL document: {query[:40]!r}")
        return httpx.Response(200, json={"data": data})

    return respond


def _sent_variables(route: respx.Route, operation: str) -> dict[str, Any]:
    """Variables of the one request whose GraphQL document is ``operation``."""
    bodies = [json.loads(call.request.content) for call in route.calls]
    matches = [b for b in bodies if _named(b["query"], operation)]
    assert len(matches) == 1  # noqa: S101
    return matches[0]["variables"]


def test_help_lists_every_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0  # noqa: S101
    for command in ("viewer", "teams", "issues", "issue", "search"):
        assert command in result.output  # noqa: S101
    issue_help = runner.invoke(app, ["issue", "--help"])
    assert issue_help.exit_code == 0  # noqa: S101
    for subcommand in ("create", "update", "comment"):
        assert subcommand in issue_help.output  # noqa: S101


def test_bare_invocation_prints_help() -> None:
    """Guard the no_args_is_help + invoke_without_command combination.

    invoke_without_command exists so ``--version`` works without a subcommand.
    A bare invocation is a *usage error*: the full help text is printed with
    exit code 2. That contract is stable because typer>=0.27 vendors its own
    CLI engine (``typer._click``) — the behavior moves only with a typer
    major bump, not with whatever unrelated ``click`` lands in the tree.
    """
    result = runner.invoke(app, [])
    assert result.exit_code == 2  # noqa: S101
    assert "Usage:" in result.output  # noqa: S101
    assert "viewer" in result.output  # noqa: S101


def test_version_flag_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0  # noqa: S101
    assert f"gtm-linear {__version__}" in result.output  # noqa: S101


def test_viewer_prints_the_authenticated_user() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["viewer"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "Alice <alice@example.com>" in result.output  # noqa: S101


def test_viewer_json_is_machine_readable() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["viewer", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout) == {  # noqa: S101
        "id": "u-1",
        "name": "Alice",
        "email": "alice@example.com",
    }


def test_teams_lists_key_name_and_id() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["teams"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "ENG" in result.output  # noqa: S101
    assert "Engineering" in result.output  # noqa: S101


def test_teams_pads_the_key_column_by_display_width() -> None:
    """A CJK key must not skew the name column; padding follows display width."""
    nodes = [
        {"id": "t-1", "key": "中文", "name": "One"},
        {"id": "t-2", "key": "ENG", "name": "Two"},
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"teams": {"nodes": nodes}}})

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["teams"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    lines = result.output.splitlines()

    def _column_of(line: str, text: str) -> int:
        # str.index measures code points; alignment lives in display columns.
        return _display_width(line[: line.index(text)])

    assert _column_of(lines[0], "One") == _column_of(lines[1], "Two")  # noqa: S101


def test_teams_json_returns_raw_nodes() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["teams", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout) == [TEAM_PAYLOAD]  # noqa: S101


def test_teams_empty_state_prints_a_message() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(team=None))
        result = runner.invoke(app, ["teams"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "no teams found" in result.output  # noqa: S101


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"teams": None}},
        {"data": {}},
        {"data": {"teams": {"nodes": [{"id": "t-1", "key": "ENG"}]}}},
        # Non-string fields would make the sanitizer raise TypeError.
        {"data": {"teams": {"nodes": [{"id": "t-1", "key": 7, "name": "Eng"}]}}},
    ],
)
def test_teams_malformed_response_fails_cleanly(body: dict[str, Any]) -> None:
    """Raw-GraphQL shape surprises must become an error line, not a traceback."""
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(200, json=body))
        result = runner.invoke(app, ["teams"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "unexpected teams response" in result.output  # noqa: S101


def test_teams_notes_when_page_info_says_more_exist() -> None:
    """The note follows pageInfo, like issues and search — not a full-page guess."""
    nodes = [
        {"id": f"t-{n}", "key": f"T{n:02d}", "name": f"Team {n}"} for n in range(3)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": nodes,
                        "pageInfo": page_info_payload(has_next=True),
                    },
                },
            },
        )

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["teams", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # The truncation note goes to stderr, so combined output has it…
    assert "more teams may exist" in result.output  # noqa: S101
    # …while stdout remains pure JSON for piped consumers.
    assert len(json.loads(result.stdout)) == 3  # noqa: S101


def test_teams_does_not_cry_wolf_on_an_exactly_full_page() -> None:
    """100 teams with pageInfo denying a next page must not print the note."""
    nodes = [
        {"id": f"t-{n}", "key": f"T{n:02d}", "name": f"Team {n}"} for n in range(100)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": nodes,
                        "pageInfo": page_info_payload(has_next=False),
                    },
                },
            },
        )

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["teams", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "more teams may exist" not in result.output  # noqa: S101
    assert len(json.loads(result.stdout)) == 100  # noqa: S101


def test_issues_open_by_default_filters_and_sorts() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "ENG-1" in result.output  # noqa: S101
    assert "complete: all matching issues fetched" in result.output  # noqa: S101
    assert "Hello" in result.output  # noqa: S101
    # Open-listing contract: unfinished states excluded, newest update first.
    assert _sent_variables(route, "ListIssues") == {  # noqa: S101
        "filter": {
            "team": {"id": {"eq": "team-1"}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
        "first": 25,
        "after": None,
        "includeArchived": False,
        "orderBy": "updatedAt",
    }


def test_issues_notes_when_more_results_exist() -> None:
    """A full page means more issues exist behind an unfollowed cursor."""
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(more=True))
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--limit", "1"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert "note: showing the first 1; more issues may exist" in result.output  # noqa: S101


def test_issues_json_stays_parseable_alongside_the_note() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(more=True))
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--limit", "1", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    # The note lands on stderr; stdout remains pure JSON.
    assert json.loads(result.stdout)[0]["identifier"] == "ENG-1"  # noqa: S101
    assert "more issues may exist" in result.output  # noqa: S101


def test_issues_all_fetches_pages_and_emits_completion_metadata() -> None:
    base = _graphql_router()
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body["query"]
        if _named(query, "ListIssues"):
            variables = body["variables"]
            calls.append(variables)
            second_page = variables["after"] is not None
            issue = issue_payload("iss-2" if second_page else "iss-1")
            issue["identifier"] = "ENG-2" if second_page else "ENG-1"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [issue],
                            "pageInfo": page_info_payload(
                                has_next=not second_page,
                                end="cursor-2" if second_page else "cursor-1",
                            ),
                        },
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--all", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    payload = json.loads(result.stdout)
    assert [item["identifier"] for item in payload["results"]] == ["ENG-1", "ENG-2"]  # noqa: S101
    assert payload["complete"] is True  # noqa: S101
    assert payload["truncated"] is False  # noqa: S101
    assert [variables["after"] for variables in calls] == [None, "cursor-1"]  # noqa: S101
    assert [variables["first"] for variables in calls] == [100, 100]  # noqa: S101


def test_bounded_issue_limit_stops_mid_page_after_following_cursor() -> None:
    base = _graphql_router()
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if _named(body["query"], "ListIssues"):
            variables = body["variables"]
            calls.append(variables)
            second_page = variables["after"] is not None
            ids = ["iss-3", "iss-4"] if second_page else ["iss-1", "iss-2"]
            nodes = [issue_payload(issue_id) for issue_id in ids]
            for offset, issue in enumerate(nodes):
                issue["identifier"] = (
                    f"ENG-{3 + offset}" if second_page else f"ENG-{1 + offset}"
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": nodes,
                            "pageInfo": page_info_payload(
                                has_next=not second_page,
                                end="cursor-2" if second_page else "cursor-1",
                            ),
                        },
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--limit", "3", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    # The JSON array contract for bounded invocations is unchanged, and only
    # three nodes are emitted even though the second response contains two.
    assert [item["identifier"] for item in json.loads(result.stdout)] == [  # noqa: S101
        "ENG-1",
        "ENG-2",
        "ENG-3",
    ]
    assert [variables["after"] for variables in calls] == [None, "cursor-1"]  # noqa: S101
    assert all(variables["first"] == 3 for variables in calls)  # noqa: S101
    assert "more issues may exist" in result.output  # noqa: S101


def test_search_notes_when_more_results_exist() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(more=True))
        result = runner.invoke(app, ["search", "zotero", "--limit", "1"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "note: showing the first 1; more results may exist" in result.output  # noqa: S101


def test_search_all_fetches_pages_and_emits_completion_metadata() -> None:
    base = _graphql_router()
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if _named(body["query"], "SearchIssues"):
            variables = body["variables"]
            calls.append(variables)
            second_page = variables["after"] is not None
            issue = issue_payload("search-2" if second_page else "search-1")
            issue["identifier"] = "ENG-2" if second_page else "ENG-1"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "searchIssues": {
                            "nodes": [issue],
                            "pageInfo": page_info_payload(
                                has_next=not second_page,
                                end="search-cursor-2"
                                if second_page
                                else "search-cursor-1",
                            ),
                        },
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["search", "term", "--all", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    payload = json.loads(result.stdout)
    assert [item["identifier"] for item in payload["results"]] == ["ENG-1", "ENG-2"]  # noqa: S101
    assert payload["complete"] is True  # noqa: S101
    assert payload["truncated"] is False  # noqa: S101
    assert [variables["after"] for variables in calls] == [None, "search-cursor-1"]  # noqa: S101
    assert [variables["first"] for variables in calls] == [100, 100]  # noqa: S101


@pytest.mark.parametrize(
    "cursor_mode",
    [
        "missing",
        "repeated",
    ],
    ids=["missing-cursor", "repeated-cursor"],
)
def test_all_marks_cursor_guard_stops_as_truncated(
    cursor_mode: str,
) -> None:
    first_cursor = None if cursor_mode == "missing" else "cursor-1"
    second_page = cursor_mode == "repeated"
    base = _graphql_router()
    calls: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if _named(body["query"], "SearchIssues"):
            after = body["variables"]["after"]
            calls.append(after)
            is_second = after is not None
            if second_page and not is_second:
                has_next = True
                end_cursor = first_cursor
            else:
                has_next = True
                end_cursor = first_cursor
            return httpx.Response(
                200,
                json={
                    "data": {
                        "searchIssues": {
                            "nodes": [issue_payload(f"search-{len(calls)}")],
                            "pageInfo": page_info_payload(
                                has_next=has_next,
                                end=end_cursor,
                            ),
                        },
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["search", "term", "--all", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    payload = json.loads(result.stdout)
    assert payload["complete"] is False  # noqa: S101
    assert payload["truncated"] is True  # noqa: S101
    assert len(calls) == (2 if second_page else 1)  # noqa: S101


def test_search_all_surfaces_stalled_pagination_error() -> None:
    base = _graphql_router()
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = json.loads(request.content)
        if _named(body["query"], "SearchIssues"):
            calls += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "searchIssues": {
                            "nodes": [],
                            "pageInfo": page_info_payload(
                                has_next=True,
                                end=f"empty-{calls}",
                            ),
                        },
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["search", "term", "--all"],
            env=AUTH,
        )
    assert result.exit_code == 1  # noqa: S101
    assert isinstance(result.exception, LinearPaginationError)  # noqa: S101
    assert calls == 3  # noqa: S101


@pytest.mark.parametrize(
    ("args", "operation", "connection"),
    [
        (["issues", "--team", "ENG", "--all", "--json"], "ListIssues", "issues"),
        (["search", "term", "--all", "--json"], "SearchIssues", "searchIssues"),
    ],
)
def test_all_json_stall_emits_partial_results_and_error(
    args: list[str],
    operation: str,
    connection: str,
) -> None:
    base = _graphql_router()
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        query = json.loads(request.content)["query"]
        if not _named(query, operation):
            return base(request)
        calls += 1
        nodes = [issue_payload("iss-1")] if calls == 1 else []
        if nodes:
            nodes[0]["identifier"] = "ENG-1"
        return httpx.Response(
            200,
            json={
                "data": {
                    connection: {
                        "nodes": nodes,
                        "pageInfo": page_info_payload(
                            has_next=True,
                            end=f"cursor-{calls}",
                        ),
                    },
                },
            },
        )

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, args, env=AUTH)

    assert result.exit_code == 1  # noqa: S101
    assert result.stderr == ""  # noqa: S101
    payload = json.loads(result.stdout)
    assert [issue["identifier"] for issue in payload["results"]] == ["ENG-1"]  # noqa: S101
    assert payload["complete"] is False  # noqa: S101
    assert payload["truncated"] is True  # noqa: S101
    assert "hasnextpage stayed true" in payload["error"].casefold()  # noqa: S101
    assert calls == 4  # noqa: S101


def test_issues_normalizes_lowercase_team_keys() -> None:
    """`--team eng` must resolve like `--team ENG`, matching `issue`'s casing."""
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issues", "--team", "eng"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "GetTeamByKey") == {"key": "ENG"}  # noqa: S101


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_team_key_is_a_usage_error(empty: str) -> None:
    """An empty --team fails at parse time, not after a network round trip."""
    with respx.mock:
        result = runner.invoke(app, ["issues", "--team", empty], env=AUTH)
    assert result.exit_code == 2  # noqa: S101
    assert "--team" in _plain(result.output)  # noqa: S101


def test_issues_state_all_keeps_completed_and_canceled() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--state", "all", "--limit", "3"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "ListIssues") == {  # noqa: S101
        "filter": {"team": {"id": {"eq": "team-1"}}},
        "first": 3,
        "after": None,
        "includeArchived": False,
        "orderBy": "updatedAt",
    }


def test_issues_filter_by_exact_state_priority_assignee_and_label() -> None:
    state = {
        "id": "state-1",
        "name": "In Progress",
        "type": "started",
        "color": "#fff",
        "position": 1.0,
    }
    with respx.mock:
        route = respx.post(API_URL).mock(
            side_effect=_graphql_router(workflow_states=[state]),
        )
        result = runner.invoke(
            app,
            [
                "issues",
                "--team",
                "ENG",
                "--state",
                "In Progress",
                "--priority",
                "high",
                "--assignee",
                "Alice",
                "--label",
                "bug",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "ListWorkflowStates")["filter"] == {  # noqa: S101
        "team": {"id": {"eq": "team-1"}},
    }
    assert _sent_variables(route, "ListIssues")["filter"] == {  # noqa: S101
        "team": {"id": {"eq": "team-1"}},
        "state": {"name": {"eq": "In Progress"}},
        "priority": {"eq": 2},
        "assignee": {"displayName": {"eq": "Alice"}},
        "labels": {"some": {"name": {"eq": "bug"}}},
    }


def test_issues_assignee_me_uses_is_me_filter() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--assignee", "me"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "ListIssues")["filter"]["assignee"] == {  # noqa: S101
        "isMe": {"eq": True},
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Urgent", 1), ("High", 2), ("medium", 3), ("Low", 4), ("0", 0), ("4", 4)],
)
def test_priority_filter_accepts_names_and_numeric_values(
    value: str,
    expected: int,
) -> None:
    assert _parse_priority(value) == expected  # noqa: S101


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--priority", "critical"],
        ["--priority", "none"],
        ["--assignee", "   "],
        ["--label", "   "],
        ["--state", "   "],
    ],
)
def test_invalid_issue_filter_values_are_usage_errors_before_requests(
    extra_args: list[str],
) -> None:
    with respx.mock:
        route = respx.post(API_URL)
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", *extra_args],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert not route.calls  # noqa: S101


@pytest.mark.parametrize("command", [["issues", "--team", "ENG"], ["search", "term"]])
def test_all_and_limit_cannot_be_combined(command: list[str]) -> None:
    with respx.mock:
        route = respx.post(API_URL)
        result = runner.invoke(app, [*command, "--all", "--limit", "3"], env=AUTH)
    assert result.exit_code == 2  # noqa: S101
    assert "cannot be combined" in _plain(result.output)  # noqa: S101
    assert not route.calls  # noqa: S101


def test_issues_verbose_prints_urls_and_descriptions() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issues", "--team", "ENG", "-v"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # Descriptions print indented so a multi-line one cannot impersonate a
    # header or table row.
    assert "description:\n   desc" in result.output  # noqa: S101
    assert "https://linear.app/x/issue/ENG-1" in result.output  # noqa: S101


def test_issues_verbose_flattens_and_indents_hostile_text() -> None:
    """Newlines in a URL or description must not forge rows in -v output."""
    hostile = issue_payload("iss-1")
    hostile["url"] = "https://linear.app/x/issue/ENG-1\nFORGED https://evil"
    hostile["description"] = "line one\nline two"
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[hostile]))
        result = runner.invoke(app, ["issues", "--team", "ENG", "-v"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # The URL is flattened to one line, so it cannot forge a new row…
    assert "\nFORGED" not in result.output  # noqa: S101
    # …and the multi-line description is indented per line, as data.
    assert "   line one" in result.output  # noqa: S101
    assert "   line two" in result.output  # noqa: S101


def test_teams_rows_flatten_hostile_names() -> None:
    nodes = [{"id": "t-1", "key": "ENG", "name": "Evil\nForged (id: x)"}]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "teams": {"nodes": nodes, "pageInfo": page_info_payload()},
                },
            },
        )

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["teams"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "\nForged" not in result.output  # noqa: S101
    assert "Evil Forged (id: x)" in result.output  # noqa: S101


def test_unreadable_env_file_fails_cleanly(tmp_path: Path) -> None:
    """A non-UTF-8 .env must not escape as a raw UnicodeDecodeError traceback."""
    (tmp_path / ".env").write_bytes(b"LINEAR_API_KEY=\xff\xfe not utf-8")
    with respx.mock:
        result = runner.invoke(app, ["viewer"])
    assert result.exit_code == 1  # noqa: S101
    assert "could not read a .env file" in result.output  # noqa: S101


def test_issues_json_emits_none_for_non_finite_priority() -> None:
    """NaN priorities must not serialize as bare NaN (invalid JSON for jq)."""
    drifted = issue_payload("iss-1")
    drifted["priority"] = float("nan")
    # httpx refuses to encode NaN into a json= body, so serve the drifted
    # payload as raw text — the client's json.loads does accept the bare NaN
    # literal a hand-crafted hostile response could carry.
    body = json.dumps(
        {"data": {"issues": {"nodes": [drifted], "pageInfo": page_info_payload()}}},
    )

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if _named(query, "GetTeamByKey"):
            return httpx.Response(
                200,
                json={"data": {"teams": {"nodes": [TEAM_PAYLOAD]}}},
            )
        return httpx.Response(200, text=body)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["issues", "--team", "ENG", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout)[0]["priority"] is None  # noqa: S101


def test_empty_env_api_key_does_not_shadow_dotenv(tmp_path: Path) -> None:
    """An exported-but-empty LINEAR_API_KEY falls through to the dotenv key."""
    (tmp_path / ".env").write_text("LINEAR_API_KEY=lin_api_dotenv\n")
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["viewer"], env={"LINEAR_API_KEY": ""})
    assert result.exit_code == 0  # noqa: S101
    assert route.calls.last.request.headers["Authorization"] == "lin_api_dotenv"  # noqa: S101


def test_empty_env_timeout_falls_back_to_default() -> None:
    """An exported-but-empty LINEAR_TIMEOUT falls through to the documented default.

    Symmetric to ``test_empty_env_api_key_does_not_shadow_dotenv``: the
    ``export X=$UNSET_VAR`` pattern is handled for endpoint fields too, so a
    valid run is not aborted with ``invalid LINEAR_* settings: timeout: ...`` —
    the empty value is ignored and the field default (``30.0``) is used, the
    request succeeds, and the viewer is printed.
    """
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["viewer"],
            env={"LINEAR_API_KEY": "lin_api_test", "LINEAR_TIMEOUT": ""},
        )
    assert result.exit_code == 0  # noqa: S101
    assert route.calls.called  # noqa: S101
    assert "Alice" in result.output  # noqa: S101
    assert "invalid LINEAR_* settings" not in result.output  # noqa: S101


@pytest.mark.parametrize("api_key_env", ["", None])
def test_empty_env_timeout_does_not_shadow_missing_key(
    api_key_env: str | None,
) -> None:
    """An empty LINEAR_TIMEOUT must not mask the missing-key guidance.

    Pass 2's ``ValidationError`` handler runs *before* the missing-key check,
    so an empty timeout that raised would blame ``timeout`` instead of
    pointing the user at ``LINEAR_API_KEY``. Both an explicitly-empty key
    (``export LINEAR_API_KEY=$UNSET_VAR``) and an unset key (not exported at
    all) must surface the actionable ``no LINEAR_API_KEY found`` guidance.
    """
    env: dict[str, str] = {"LINEAR_TIMEOUT": ""}
    if api_key_env is not None:
        env["LINEAR_API_KEY"] = api_key_env
    with respx.mock:
        result = runner.invoke(app, ["viewer"], env=env)
    assert result.exit_code == 1  # noqa: S101
    assert "no LINEAR_API_KEY found" in result.output  # noqa: S101
    assert "invalid LINEAR_* settings" not in result.output  # noqa: S101


def test_issue_json_lists_issue_dicts() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issues", "--team", "ENG", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    payload = json.loads(result.stdout)
    assert payload[0]["identifier"] == "ENG-1"  # noqa: S101
    assert payload[0]["state"] == "In Progress"  # noqa: S101
    assert payload[0]["assignee"] == "Alice"  # noqa: S101


def _operation_names(route: respx.Route) -> list[str]:
    """Names of GraphQL operations sent through a mocked route."""
    names: list[str] = []
    for call in route.calls:
        query = json.loads(call.request.content)["query"]
        match = re.search(r"(?:query|mutation)\s+(\w+)", query)
        assert match is not None  # noqa: S101
        names.append(match.group(1))
    return names


def test_issue_create_json_preview_sends_no_mutation() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            [
                "issue",
                "create",
                "--team",
                "eng",
                "--title",
                "Investigate alert",
                "--description",
                "From agent",
                "--priority",
                "2",
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    preview = json.loads(result.stdout)
    assert preview == {  # noqa: S101
        "applied": False,
        "operation": "issue.create",
        "target": {
            "teamKey": "ENG",
            "teamName": "Engineering",
            "teamId": "team-1",
        },
        "payload": {
            "teamId": "team-1",
            "title": "Investigate alert",
            "description": "From agent",
            "priority": 2,
        },
    }
    assert _operation_names(route) == ["GetTeamByKey"]  # noqa: S101


def test_issue_create_human_preview_is_explicitly_dry_run() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issue", "create", "--team", "ENG", "--title", "Dry run"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert "DRY RUN: no mutation sent" in result.stdout  # noqa: S101
    assert "use --apply to execute" in result.stdout  # noqa: S101
    assert '"title": "Dry run"' in result.stdout  # noqa: S101
    assert _operation_names(route) == ["GetTeamByKey"]  # noqa: S101


def test_issue_create_apply_returns_resource_and_mutates() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            [
                "issue",
                "create",
                "--team",
                "ENG",
                "--title",
                "Create me",
                "--apply",
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout)["identifier"] == "ENG-1"  # noqa: S101
    assert _operation_names(route) == ["GetTeamByKey", "CreateIssue"]  # noqa: S101
    variables = _sent_variables(route, "CreateIssue")
    assert variables == {  # noqa: S101
        "input": {"teamId": "team-1", "title": "Create me"},
    }


@pytest.mark.parametrize(
    ("args", "api_operation", "operation", "target"),
    [
        (
            ["issue", "create", "--team", "ENG", "--title", "x", "--apply", "--json"],
            "CreateIssue",
            "issue.create",
            {"teamKey": "ENG", "teamName": "Engineering", "teamId": "team-1"},
        ),
        (
            ["issue", "update", "ENG-1", "--priority", "2", "--apply", "--json"],
            "UpdateIssue",
            "issue.update",
            {"identifier": "ENG-1", "id": "iss-1"},
        ),
        (
            ["issue", "comment", "ENG-1", "--body", "x", "--apply", "--json"],
            "CreateComment",
            "issue.comment",
            {"identifier": "ENG-1", "id": "iss-1"},
        ),
    ],
)
def test_issue_apply_json_graphql_failure_emits_error_envelope(
    args: list[str],
    api_operation: str,
    operation: str,
    target: dict[str, str],
) -> None:
    base = _graphql_router()

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if _named(query, api_operation):
            return httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Authentication required",
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        },
                    ],
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, args, env=AUTH)

    assert result.exit_code == 1  # noqa: S101
    assert result.stderr == ""  # noqa: S101
    assert json.loads(result.stdout) == {  # noqa: S101
        "applied": False,
        "operation": operation,
        "target": target,
        "error": "GraphQL error: Authentication required",
    }


def test_issue_comment_json_validation_failure_emits_error_envelope() -> None:
    base = _graphql_router()

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if _named(query, "CreateComment"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {"success": True, "comment": None},
                    },
                },
            )
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["issue", "comment", "ENG-1", "--body", "x", "--apply", "--json"],
            env=AUTH,
        )

    assert result.exit_code == 1  # noqa: S101
    assert result.stderr == ""  # noqa: S101
    payload = json.loads(result.stdout)
    assert payload["applied"] is False  # noqa: S101
    assert payload["operation"] == "issue.comment"  # noqa: S101
    assert payload["target"] == {"identifier": "ENG-1", "id": "iss-1"}  # noqa: S101
    assert "unexpected response shape from Linear" in payload["error"]  # noqa: S101


def test_issue_update_json_http_failure_emits_error_envelope() -> None:
    base = _graphql_router()

    def respond(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if _named(query, "UpdateIssue"):
            return httpx.Response(503, text="service unavailable")
        return base(request)

    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["issue", "update", "ENG-1", "--priority", "2", "--apply", "--json"],
            env=AUTH,
        )

    assert result.exit_code == 1  # noqa: S101
    assert result.stderr == ""  # noqa: S101
    payload = json.loads(result.stdout)
    assert payload["applied"] is False  # noqa: S101
    assert payload["operation"] == "issue.update"  # noqa: S101
    assert payload["target"] == {"identifier": "ENG-1", "id": "iss-1"}  # noqa: S101
    assert payload["error"] == "HTTP error: 503"  # noqa: S101


def test_issue_update_json_preview_supports_explicit_clear() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issue", "update", "eng-1", "--clear-description", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    preview = json.loads(result.stdout)
    assert preview["applied"] is False  # noqa: S101
    assert preview["target"] == {"identifier": "ENG-1", "id": "iss-1"}  # noqa: S101
    assert preview["payload"] == {"description": None}  # noqa: S101
    assert _operation_names(route) == ["GetIssue"]  # noqa: S101


def test_issue_update_apply_returns_resource_and_uses_resolved_id() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issue", "update", "eng-1", "--priority", "3", "--apply", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout)["identifier"] == "ENG-1"  # noqa: S101
    assert _operation_names(route) == ["GetIssue", "UpdateIssue"]  # noqa: S101
    assert _sent_variables(route, "UpdateIssue") == {  # noqa: S101
        "id": "iss-1",
        "input": {"priority": 3},
    }


def test_issue_comment_json_preview_sends_no_mutation() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issue", "comment", "ENG-1", "--body", "Please investigate", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    preview = json.loads(result.stdout)
    assert preview["applied"] is False  # noqa: S101
    assert preview["operation"] == "issue.comment"  # noqa: S101
    assert preview["target"] == {"identifier": "ENG-1", "id": "iss-1"}  # noqa: S101
    assert preview["payload"] == {  # noqa: S101
        "issueId": "iss-1",
        "body": "Please investigate",
    }
    assert _operation_names(route) == ["GetIssue"]  # noqa: S101


def test_issue_comment_apply_returns_comment_resource() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            [
                "issue",
                "comment",
                "ENG-1",
                "--body",
                "A comment",
                "--apply",
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout) == {  # noqa: S101
        "id": "comment-1",
        "body": "A comment",
        "url": "https://linear.app/x/comment-1",
        "createdAt": "2025-01-01T00:00:00+00:00",
    }
    assert _operation_names(route) == ["GetIssue", "CreateComment"]  # noqa: S101
    assert _sent_variables(route, "CreateComment") == {  # noqa: S101
        "input": {"issueId": "iss-1", "body": "A comment"},
    }


@pytest.mark.parametrize(
    "args",
    [
        ["issue", "create", "--team", "ENG"],
        ["issue", "create", "--team", "ENG", "--title", "  "],
        ["issue", "update", "ENG-1"],
        [
            "issue",
            "update",
            "ENG-1",
            "--description",
            "new",
            "--clear-description",
        ],
        ["issue", "update", "ENG-1", "--priority", "5"],
        ["issue", "comment", "ENG-1", "--body", "  "],
    ],
)
def test_write_validation_errors_are_non_interactive_and_pre_request(
    args: list[str],
) -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, args, env=AUTH, input="")
    assert result.exit_code == 2  # noqa: S101
    assert not route.calls  # noqa: S101


@pytest.mark.parametrize(
    ("args", "team", "target_kind", "operations"),
    [
        (
            ["issue", "create", "--team", "ENG", "--title", "No team"],
            None,
            "team",
            ["GetTeamByKey"],
        ),
        (
            ["issue", "update", "ENG-999", "--title", "No issue"],
            TEAM_PAYLOAD,
            "issue",
            ["GetIssue"],
        ),
    ],
)
def test_write_commands_fail_cleanly_for_unresolved_targets(
    args: list[str],
    team: dict[str, Any] | None,
    target_kind: str,
    operations: list[str],
) -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            side_effect=_graphql_router(
                team=team,
                issue_exists=target_kind != "issue",
            ),
        )
        result = runner.invoke(app, args, env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert _operation_names(route) == operations  # noqa: S101


def _empty_search(request: httpx.Request) -> httpx.Response:
    """A SearchIssues page with zero nodes."""
    return httpx.Response(
        200,
        json={"data": {"searchIssues": {"nodes": [], "pageInfo": page_info_payload()}}},
    )


def test_empty_results_json_is_an_empty_list() -> None:
    """JSON of zero results must be ``[]``, not the human ``no issues found`` line.

    The ``as_json`` branch deliberately runs before the human empty-state
    check, so piped consumers get a parseable list in every case.
    """
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[]))
        result = runner.invoke(app, ["issues", "--team", "ENG", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout) == []  # noqa: S101

    with respx.mock:
        respx.post(API_URL).mock(side_effect=_empty_search)
        result = runner.invoke(app, ["search", "zotero", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout) == []  # noqa: S101


def test_empty_results_human_output_states_so() -> None:
    """The human rendering of zero results is an explicit message, not silence."""
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "no issues found" in result.output  # noqa: S101

    with respx.mock:
        respx.post(API_URL).mock(side_effect=_empty_search)
        result = runner.invoke(app, ["search", "zotero"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "no issues found" in result.output  # noqa: S101


def test_issues_table_cells_are_whitespace_safe() -> None:
    messy = issue_payload("iss-1")
    messy["title"] = "Multi\nline\ttitle"
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[messy]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # Newlines and tabs are collapsed so one issue cannot break the table.
    assert "Multi line title" in result.output  # noqa: S101
    assert "Multi\nline" not in result.output  # noqa: S101


def test_issues_table_aligns_wide_characters() -> None:
    """CJK titles occupy double columns; padding must follow display width."""
    wide = issue_payload("iss-1")
    wide["title"] = "日本語"  # 3 code points, 6 display columns
    narrow = issue_payload("iss-2")
    narrow["identifier"] = "ENG-2"
    narrow["title"] = "abcdef"  # 6 code points, 6 display columns
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[wide, narrow]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    lines = result.output.splitlines()
    # The TITLE column starts at the same offset in both rows and both rows
    # occupy the same display width — code-point padding would skew both.
    assert lines[2].index("日本語") == lines[3].index("abcdef")  # noqa: S101
    assert _display_width(lines[2]) == _display_width(lines[3])  # noqa: S101


def test_priority_label_mapping() -> None:
    """Linear sends priorities as floats; 0 and None mean unset."""
    cases: list[tuple[float | None, str]] = [
        (None, "-"),
        (0.0, "-"),
        (1.0, "Urgent"),
        (2.0, "High"),
        (3.0, "Medium"),
        (4.0, "Low"),
        (5.0, "5"),  # future/unknown levels degrade to the raw number
        (float("nan"), "-"),  # legal floats a drifted payload could carry
        (float("inf"), "-"),
    ]
    for priority, expected in cases:
        assert _priority_label(priority) == expected  # noqa: S101


def test_display_width_counts_columns_not_code_points() -> None:
    """Alignment must follow terminal columns: CJK 2, zero-width marks 0."""
    assert _display_width("AB") == 2  # noqa: S101
    assert _display_width("中文") == 4  # noqa: S101
    assert _display_width("cafe\u0301") == 4  # noqa: S101
    assert _display_width("🚀") == 2  # noqa: S101
    # VS16 has combining class 0 but is still zero-width (category Mn).
    assert _display_width("\ufe0f") == 0  # noqa: S101
    # ZWJ is kept and counted as zero-width; a joined run sums its parts —
    # best-effort, matching terminals that do not ligate it into one glyph.
    assert _display_width("👨\u200d👩") == 4  # noqa: S101
    # Narrow base + VS16 requests emoji presentation: rendered at 2 columns.
    assert _display_width("❤\ufe0f") == 2  # noqa: S101
    assert _display_width("☎\ufe0f") == 2  # noqa: S101
    # An already-wide base keeps its own width (no double count)…
    assert _display_width("中\ufe0f") == 2  # noqa: S101
    # …and a zero-width base stays zero: VS16 is just a mark there.
    assert _display_width("\u0301\ufe0f") == 0  # noqa: S101


def test_issues_table_strips_terminal_control_characters() -> None:
    """Titles are other members' text; ESC sequences must not reach the tty."""
    hostile = issue_payload("iss-1")
    # \u202e is a bidi override; \u061c the Arabic Letter Mark, another bidi
    # control. (ZWJ \u200d is deliberately kept — see _CONTROL_CHARS.)
    hostile["title"] = "".join(
        (
            "Evil",
            chr(27),
            "[31mTi",
            chr(13),
            "tle",
            chr(7),
            chr(0x202E),
            chr(0x061C),
        ),
    )
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[hostile]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # Whole sequences vanish; a bare carriage return (which could overwrite
    # the printed line) and the bidi controls are stripped too.
    assert "EvilTitle" in result.output  # noqa: S101
    assert "\x1b" not in result.output  # noqa: S101
    assert "\r" not in result.output  # noqa: S101
    assert "\u202e" not in result.output  # noqa: S101
    assert "\u061c" not in result.output  # noqa: S101


def test_sanitizer_neutralizes_unterminated_sequences() -> None:
    """An ESC with no complete sequence behind it must never reach the tty.

    Both tails below are unterminated: the OSC body never meets BEL or ST, so
    the ESC and its ``]`` are consumed by the two-character escape
    alternative; the CSI tail never meets a final byte, so its lone ESC is
    stripped by the control-character pass. What survives is inert bracket
    text — ugly, but harmless. Only the never-an-ESC property is asserted, so
    an improved regex does not have to preserve the exact residue.
    """
    hostile = issue_payload("iss-1")
    hostile["title"] = "Bad\x1b]0;evil and \x1b[31"
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[hostile]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "\x1b" not in result.output  # noqa: S101
    assert "0;evil" in result.output  # noqa: S101


def test_issue_output_strips_terminal_control_characters() -> None:
    hostile = issue_payload("iss-1")
    hostile["description"] = "Evil\x1b[2Kdescription\x1b]0;pwned\x07 line"
    hostile["id"] = "iss\x1b[31m-1"  # ids are remote strings too
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": hostile}}),
        )
        result = runner.invoke(app, ["issue", "ENG-1"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "\x1b" not in result.output  # noqa: S101
    assert "Evildescription line" in result.output  # noqa: S101


def test_sanitizer_strips_invisible_format_characters() -> None:
    """Risky Cf code points must go (soft hyphen, joiners, BOM, tags).

    ZWJ and ZWNJ stay: ZWJ holds emoji sequences together, and ZWNJ carries
    word-splitting meaning in Persian, Kurdish, and several Indic scripts.
    """
    hostile = issue_payload("iss-1")
    hostile["title"] = "A\u00adB\u2060C\u200cD\u200dE\ufeffF\U000e0041G"
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[hostile]))
        result = runner.invoke(app, ["issues", "--team", "ENG"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # Soft hyphen, word joiner, BOM, and the tag character vanish…
    assert "AB" in result.output  # noqa: S101
    assert "\u00ad" not in result.output  # noqa: S101
    assert "\ufeff" not in result.output  # noqa: S101
    assert "\U000e0041" not in result.output  # noqa: S101
    # …while ZWNJ and ZWJ survive, between C/D and D/E respectively.
    assert "C\u200cD\u200dE" in result.output  # noqa: S101


def test_issues_verbose_handles_missing_description() -> None:
    """A null description renders the documented '-' placeholder, not a crash."""
    payload = issue_payload("iss-1")
    payload["description"] = None
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(issues=[payload]))
        result = runner.invoke(app, ["issues", "--team", "ENG", "-v"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "   -" in result.output  # noqa: S101


def test_real_env_endpoint_error_wins_over_dotenv_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two-pass resolution end to end: dotenv supplies the key, real env owns the endpoint.

    A malformed LINEAR_BASE_URL in the real environment must still fail even
    when a dotenv file satisfies auth — the endpoint never comes from dotenv.
    """
    (tmp_path / ".env").write_text("LINEAR_API_KEY=lin_api_dotenv\n")
    monkeypatch.setattr(sys, "argv", ["gtm-linear", "viewer"])
    monkeypatch.setenv("LINEAR_BASE_URL", "http://[zz]/graphql")
    with respx.mock:
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "error: invalid LINEAR_BASE_URL" in capsys.readouterr().err  # noqa: S101


def test_issue_output_flattens_newlines_in_single_line_fields() -> None:
    """A title carrying newlines must not forge `state:` / `priority:` rows."""
    hostile = issue_payload("iss-1")
    hostile["title"] = "x\n state: Done\n priority: Urgent"
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": hostile}}),
        )
        result = runner.invoke(app, ["issue", "ENG-1"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # The whole hostile title lands on one line, as data — not as field rows:
    # unflattened, "Done" would terminate a forged line of its own.
    assert "ENG-1: x state: Done priority: Urgent" in result.output  # noqa: S101
    assert "Done\n" not in result.output  # noqa: S101


def test_viewer_flattens_newlines_in_remote_fields() -> None:
    """A user name cannot forge an `(id: ...)` row via an embedded newline."""
    hostile = user_payload()
    hostile["name"] = "Alice\n id: forged"
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": hostile}}),
        )
        result = runner.invoke(app, ["viewer"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "Alice id: forged" in result.output  # noqa: S101
    assert "\n id:" not in result.output  # noqa: S101


def test_issues_unknown_team_fails_with_a_clean_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router(team=None))
        result = runner.invoke(app, ["issues", "--team", "NOPE"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no Linear team with key 'NOPE'" in result.output  # noqa: S101


@pytest.mark.parametrize(
    "prefix",
    [["issues", "--team", "ENG"], ["search", "zotero"]],
)
@pytest.mark.parametrize("bad_limit", ["0", "101", "-5"])
def test_limit_out_of_range_is_a_usage_error(prefix: list[str], bad_limit: str) -> None:
    # No routes are registered: respx raises on any request, so a bug that let
    # parsing succeed would hit AllMockedAssertionError, not the network.
    with respx.mock:
        result = runner.invoke(
            app,
            [*prefix, "--limit", bad_limit],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert "--limit" in _plain(result.output)  # noqa: S101


def test_state_rejects_unknown_values() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["issues", "--team", "ENG", "--state", "bogus"],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert "--state" in _plain(result.output)  # noqa: S101
    assert not any(
        _named(json.loads(call.request.content)["query"], "ListIssues")
        for call in route.calls
    )  # noqa: S101


def test_issue_normalizes_and_fetches_by_identifier() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issue", "eng-1"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    # Lowercase input is normalized before the lookup.
    assert "ENG-1: Hello" in result.output  # noqa: S101
    assert _sent_variables(route, "GetIssue") == {"id": "ENG-1"}  # noqa: S101


def test_issue_passes_uuids_through_unchanged() -> None:
    """Uppercasing a lowercase UUID would corrupt the lookup — pass it as-is."""
    uuid = "2f6a1a41-59a3-4568-8e43-c315ee80e3ec"
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issue", uuid], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "GetIssue") == {"id": uuid}  # noqa: S101


def test_issue_json_is_one_dict() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["issue", "ENG-1", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout)["identifier"] == "ENG-1"  # noqa: S101


def test_issue_not_found_via_null_issue_is_a_clean_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}}),
        )
        result = runner.invoke(app, ["issue", "ENG-404"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no issue found with identifier ENG-404" in result.output  # noqa: S101


def test_issue_not_found_via_api_error_is_a_clean_error() -> None:
    """Linear reports unknown ids as a GraphQL error, not a null issue.

    The payload below is captured verbatim from Linear's live API (2026-10-02)
    so the wording this heuristic matches is pinned to reality.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Entity not found: Issue",
                            "extensions": {
                                "type": "invalid input",
                                "code": "INPUT_ERROR",
                                "statusCode": 400,
                                "userError": True,
                                "userPresentableMessage": (
                                    "Could not find referenced Issue."
                                ),
                            },
                        },
                    ],
                },
            ),
        )
        result = runner.invoke(app, ["issue", "ENG-404"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no issue found with identifier ENG-404" in result.output  # noqa: S101


def test_issue_not_found_via_extension_wording_is_a_clean_error() -> None:
    """A reworded message still matches through userPresentableMessage."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Look-up failed",
                            "extensions": {
                                "userPresentableMessage": "Could not find referenced Issue.",
                            },
                        },
                    ],
                },
            ),
        )
        result = runner.invoke(app, ["issue", "ENG-404"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no issue found with identifier ENG-404" in result.output  # noqa: S101


def test_issue_other_entity_not_found_is_not_issue_not_found() -> None:
    """A not-found for a different entity reaches the generic error line."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"errors": [{"message": "Entity not found: Team"}]},
            ),
        )
        result = runner.invoke(app, ["issue", "ENG-1"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no issue found" not in result.output  # noqa: S101
    assert isinstance(result.exception, LinearAPIError)  # noqa: S101


def test_issue_unrelated_api_error_reaches_main_mapping(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unrelated API errors must not be translated into the not-found message.

    An error merely flavored like not-found wording risks the same
    misreport; a genuinely unrelated one (auth, rate limit) must propagate
    untouched to main()'s one-line error mapping.
    """
    _prepare_main_env(["issue", "ENG-1"], monkeypatch)
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]}),
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    err = capsys.readouterr().err
    assert "error: GraphQL error: boom" in err  # noqa: S101
    assert "no issue found" not in err  # noqa: S101


@pytest.mark.parametrize(
    ("invocation", "expected"),
    [
        # No term at all fails in argument collection, before the body check.
        (["search"], "Missing argument"),
        (["search", ""], "search term must not be empty"),
        (["search", "   "], "search term must not be empty"),
    ],
)
def test_empty_search_term_is_a_usage_error(
    invocation: list[str],
    expected: str,
) -> None:
    """No or blank search term fails at parse time, like an empty --team."""
    with respx.mock:
        result = runner.invoke(app, invocation, env=AUTH)
    assert result.exit_code == 2  # noqa: S101
    assert expected in result.output  # noqa: S101


def test_search_joins_unquoted_multiword_terms() -> None:
    """`search onboarding flow` searches for "onboarding flow", no quotes needed."""
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["search", "onboarding", "flow"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "SearchIssues")["term"] == "onboarding flow"  # noqa: S101


def test_search_accepts_terms_that_look_like_options() -> None:
    """`search "-foo"` must search for "-foo", not demand the `--` separator."""
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["search", "-foo"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert _sent_variables(route, "SearchIssues")["term"] == "-foo"  # noqa: S101


@pytest.mark.network
def test_issue_not_found_wording_against_live_api() -> None:
    """Pin Linear's live not-found wording (scheduled CI run; needs auth).

    The not-found heuristic matches Linear's message wording, so a rewording
    upstream would silently degrade `issue` to the generic error line. This
    live check makes that drift surface in the weekly network run instead.
    The probe uses the CLI's own first-pass settings class, so the skip
    decision and the code under test agree on where a key may come from.
    """
    if not _DotenvApiKey().api_key.get_secret_value():
        pytest.skip("no LINEAR_API_KEY available for the live check")
    result = runner.invoke(app, ["issue", "ZZZ-999999"])
    assert result.exit_code == 1  # noqa: S101
    assert "no issue found with identifier ZZZ-999999" in result.output  # noqa: S101


def test_search_renders_hits_and_forwards_limit() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["search", "zotero", "--limit", "3"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert "ENG-1" in result.output  # noqa: S101
    assert _sent_variables(route, "SearchIssues") == {  # noqa: S101
        "term": "zotero",
        "first": 3,
        "after": None,
    }


def test_search_verbose_prints_urls_and_descriptions() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["search", "zotero", "-v"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "description:\n   desc" in result.output  # noqa: S101
    assert "https://linear.app/x/issue/ENG-1" in result.output  # noqa: S101


def test_search_json_lists_issue_dicts() -> None:
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["search", "zotero", "--json"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert json.loads(result.stdout)[0]["title"] == "Hello"  # noqa: S101


def test_missing_api_key_exits_with_guidance() -> None:
    """The autouse fixture has already cleared LINEAR_* and isolated the cwd."""
    with respx.mock:
        result = runner.invoke(app, ["viewer"])
    assert result.exit_code == 1  # noqa: S101
    assert "no LINEAR_API_KEY found" in result.output  # noqa: S101


def test_unrelated_settings_errors_are_not_misreported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ValidationError may be translated into settings error lines."""

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("dns exploded")

    monkeypatch.setattr("gtm_linear.cli._DotenvApiKey", _raise)
    with respx.mock:
        result = runner.invoke(app, ["viewer"])
    assert isinstance(result.exception, RuntimeError)  # noqa: S101
    assert "no LINEAR_API_KEY found" not in result.output  # noqa: S101
    assert "invalid LINEAR_* settings" not in result.output  # noqa: S101


def test_bad_env_value_is_not_misreported_as_missing_key() -> None:
    """A valid key with a bad LINEAR_TIMEOUT must not produce key guidance."""
    with respx.mock:
        result = runner.invoke(
            app,
            ["viewer"],
            env={"LINEAR_API_KEY": "lin_api_test", "LINEAR_TIMEOUT": "abc"},
        )
    assert result.exit_code == 1  # noqa: S101
    assert "invalid LINEAR_* settings: timeout:" in result.output  # noqa: S101
    assert "no LINEAR_API_KEY found" not in result.output  # noqa: S101


def test_whitespace_only_api_key_is_missing_not_invalid() -> None:
    """A whitespace-only key maps to the missing-key guidance, not a traceback."""
    with respx.mock:
        result = runner.invoke(app, ["viewer"], env={"LINEAR_API_KEY": "   "})
    assert result.exit_code == 1  # noqa: S101
    assert "no LINEAR_API_KEY found" in result.output  # noqa: S101


def test_malformed_api_key_is_one_clean_line() -> None:
    """A pasted 'Bearer ' prefix is rejected by LinearClient at construction."""
    with respx.mock:
        result = runner.invoke(
            app,
            ["viewer"],
            env={"LINEAR_API_KEY": "Bearer lin_api_x"},
        )
    assert result.exit_code == 1  # noqa: S101
    assert "error: invalid LINEAR_API_KEY:" in result.output  # noqa: S101
    assert "Traceback" not in result.output  # noqa: S101


@pytest.mark.parametrize("env_file", [".env", ".env.local"])
def test_env_file_cannot_redirect_the_endpoint(tmp_path: Path, env_file: str) -> None:
    """Dotenv may carry the key, but never the endpoint.

    A console script runs from arbitrary directories; an untrusted checkout's
    dotenv file must not be able to send the Authorization header to another
    host.
    """
    (tmp_path / env_file).write_text(
        "LINEAR_API_KEY=lin_api_dotenv\nLINEAR_BASE_URL=https://evil.example/graphql\n",
    )
    with respx.mock:
        # Only the default endpoint is routed — a request to evil.example
        # would raise AllMockedAssertionError and fail this test.
        route = respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["viewer"])
    assert result.exit_code == 0  # noqa: S101
    # The dotenv key was the one actually sent — to the default endpoint.
    assert route.calls.last.request.headers["Authorization"] == "lin_api_dotenv"  # noqa: S101


def test_env_file_cannot_break_endpoint_settings(
    tmp_path: Path,
) -> None:
    """A hostile .env cannot even cause a parse error in fields it cannot affect.

    A bad LINEAR_TIMEOUT in .env must be ignored (the real environment decides
    endpoint settings), not reported as invalid configuration.
    """
    (tmp_path / ".env").write_text(
        "LINEAR_API_KEY=lin_api_dotenv\nLINEAR_TIMEOUT=abc\n",
    )
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        result = runner.invoke(app, ["viewer"])
    assert result.exit_code == 0  # noqa: S101
    assert "Alice" in result.output  # noqa: S101


def test_real_env_still_overrides_the_endpoint() -> None:
    """LINEAR_BASE_URL from the real environment stays honored (proxies, tests)."""
    proxied = "http://proxy.test/graphql"
    with respx.mock:
        respx.post(proxied).mock(side_effect=_graphql_router())
        result = runner.invoke(
            app,
            ["viewer"],
            env={"LINEAR_API_KEY": "lin_api_test", "LINEAR_BASE_URL": proxied},
        )
    assert result.exit_code == 0  # noqa: S101
    assert "Alice" in result.output  # noqa: S101


def _prepare_main_env(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the real console-script entry point at a deterministic argv/env."""
    monkeypatch.setattr(sys, "argv", ["gtm-linear", *argv])
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.delenv("LINEAR_BASE_URL", raising=False)
    monkeypatch.delenv("LINEAR_TIMEOUT", raising=False)


def test_main_maps_api_errors_to_stderr_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main_env(["viewer"], monkeypatch)
    with respx.mock:
        # The message carries an escape sequence: exception text comes from
        # the remote, so it must be sanitized like any other remote string.
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"errors": [{"message": "boom\x1b[31mred"}]},
            ),
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    # result.output interleaves streams, so assert on real stderr here.
    err = capsys.readouterr().err
    assert "error: GraphQL error: boom" in err  # noqa: S101
    assert "\x1b" not in err  # noqa: S101


def test_main_maps_http_status_errors_to_stderr_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main_env(["viewer"], monkeypatch)
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(401, text="nope"))
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "error: HTTP error: 401" in capsys.readouterr().err  # noqa: S101


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("no route to host"), "no route to host"),
        (httpx.ReadTimeout("timed out"), "timed out"),
    ],
)
def test_main_maps_transport_errors_to_stderr_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected: str,
) -> None:
    """Offline/DNS/timeout failures must not escape as raw tracebacks."""
    _prepare_main_env(["viewer"], monkeypatch)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=failure)
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    err = capsys.readouterr().err
    assert "error: could not reach Linear" in err  # noqa: S101
    assert expected in err  # noqa: S101


def test_main_version_flag_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main_env(["--version"], monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0  # noqa: S101
    assert f"gtm-linear {__version__}" in capsys.readouterr().out  # noqa: S101


def test_main_ctrl_c_exits_130(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ctrl-C from inside a real command lands as exit 130, quietly.

    The vendored Typer/Click standalone loop performs the conversion itself
    (verified live); this pins the user-facing contract end to end through
    main() and the real app() loop.
    """

    def _interrupt(self: LinearWorkflow) -> None:
        raise KeyboardInterrupt

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr(LinearWorkflow, "get_viewer", _interrupt)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 130  # noqa: S101
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err  # noqa: S101
    assert "Aborted!" not in captured.out  # noqa: S101


def test_main_fallback_branch_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main()'s own KeyboardInterrupt branch, bypassing Typer's conversion.

    The standalone loop normally converts the interrupt first; patching
    ``app`` targets the fallback that would own the contract if a future
    engine version stopped doing so.
    """

    def _interrupt() -> None:
        raise KeyboardInterrupt

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr("gtm_linear.cli.app", _interrupt)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 130  # noqa: S101
    assert "Traceback" not in capsys.readouterr().err  # noqa: S101


def test_main_maps_schema_drift_to_a_clean_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A response that no longer parses into the generated models is one line.

    Linear shipping a shape change would otherwise surface as a pydantic
    traceback (LinearSettings validation errors are handled in _workflow and
    never reach this branch).
    """
    _prepare_main_env(["issue", "ENG-1"], monkeypatch)
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                # Well-formed envelope, but the issue fields are nonsense.
                json={"data": {"issue": {"id": 123, "identifier": []}}},
            ),
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "unexpected response shape from Linear" in capsys.readouterr().err  # noqa: S101


def test_main_sanitizes_schema_drift_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even pydantic-supplied loc/msg text must not carry escape sequences."""

    def _drift() -> None:
        raise ValidationError.from_exception_data(
            "IssueFields",
            [
                {
                    "type": "string_type",
                    "loc": ("evil\x1b[31mfield",),
                    "input": 1,
                    "msg": "Input should be a valid string\x1b[2K",
                },
            ],
        )

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr("gtm_linear.cli.app", _drift)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1  # noqa: S101
    err = capsys.readouterr().err
    assert "unexpected response shape from Linear" in err  # noqa: S101
    assert "\x1b" not in err  # noqa: S101


def test_main_maps_non_json_responses_to_a_clean_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 200 with an HTML body (captive portal/proxy) is not a traceback.

    LinearClient wraps unparseable bodies in LinearResponseError, which
    main() maps to the standard error line — this pins that end to end.
    """
    _prepare_main_env(["viewer"], monkeypatch)
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(200, text="<html>"))
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "error: Invalid response format" in capsys.readouterr().err  # noqa: S101


def test_main_invalid_url_fallback_is_generic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An InvalidURL from anywhere but LINEAR_BASE_URL gets a neutral message.

    _workflow() validates LINEAR_BASE_URL itself and blames that variable
    precisely; this branch is the fallback for other malformed URLs, such as
    a hostile redirect target.
    """

    def _bad_redirect() -> None:
        raise httpx.InvalidURL("Invalid redirect location")

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr("gtm_linear.cli.app", _bad_redirect)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "error: invalid URL: Invalid redirect location" in capsys.readouterr().err  # noqa: S101


def test_main_maps_stream_encoding_failures_to_a_clean_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-UTF-8 output stream (legacy code page, PYTHONIOENCODING=ascii) is
    one error line, not a traceback — even though str(exc) carries the very
    character that broke the stream, so the message must be escaped.
    """

    def _unencodable(*args: object, **kwargs: object) -> None:
        raise UnicodeEncodeError("ascii", "中文", 0, 1, "ordinal not in range(128)")

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr("gtm_linear.cli.typer.echo", _unencodable)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=_graphql_router())
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    err = capsys.readouterr().err
    assert "error: output stream cannot encode this text" in err  # noqa: S101
    assert "中文" not in err  # noqa: S101
    assert "\\u4e2d" in err  # noqa: S101


def test_main_maps_invalid_base_url_to_stderr_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed LINEAR_BASE_URL must become one error line, not a traceback.

    Bracket-host URLs like ``http://[zz]`` fail httpx's URL parsing with
    InvalidURL, which sits outside the HTTPError family.
    """
    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setenv("LINEAR_BASE_URL", "http://[zz]/graphql")
    with respx.mock:
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 1  # noqa: S101
    assert "error: invalid LINEAR_BASE_URL" in capsys.readouterr().err  # noqa: S101


def test_main_fallback_branch_catches_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main()'s own BrokenPipeError branch, bypassing Typer's pacification.

    The vendored Typer/Click loop already handles EPIPE itself (verified
    live); patching ``app`` targets the fallback that would own the contract
    if a future engine version stopped doing so. errno.EPIPE is included, as
    a real closed pipe raises it — the devnull redirect inside is suppressed
    under test capture (stdout is a StringIO), which is harmless: nothing
    more is written either way.
    """

    def _closed_pipe() -> None:
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    _prepare_main_env(["viewer"], monkeypatch)
    monkeypatch.setattr("gtm_linear.cli.app", _closed_pipe)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1  # noqa: S101
    captured = capsys.readouterr()
    assert "error:" not in captured.err  # noqa: S101
    assert "Traceback" not in captured.err  # noqa: S101
