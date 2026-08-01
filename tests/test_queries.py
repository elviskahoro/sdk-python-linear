"""Read-path tests.

``respx.mock`` is used as a context manager rather than a decorator: the decorator is
untyped, so every use needed a ``# type: ignore[misc]``.
"""

from __future__ import annotations

import json

import httpx
import respx

from gtm_linear import LinearClient, LinearQueries, PaginationOrderBy
from tests.conftest import API_URL, issue_payload, page_info_payload, user_payload


def test_queries_wildcard_exports_search_result_type() -> None:
    namespace: dict[str, object] = {}
    exec("from gtm_linear.queries import *", namespace)  # noqa: S102
    assert "IssueSearchResultFields" in namespace  # noqa: S101


async def test_get_issue_returns_parsed_issue() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": issue_payload()}}),
        )
        async with LinearClient(api_key="key") as client:
            issue = await LinearQueries(client).get_issue("iss-1")

    assert issue is not None  # noqa: S101
    assert issue.identifier == "ENG-1"  # noqa: S101
    # `state` is a real object now, not a string flattened from `state { name }`.
    assert issue.state.name == "In Progress"  # noqa: S101
    assert issue.state.type == "started"  # noqa: S101
    # Linear's schema types priority as Float!, so the model does too.
    assert issue.priority == 2.0  # noqa: S101
    assert isinstance(issue.priority, float)  # noqa: S101
    assert issue.assignee is not None  # noqa: S101
    assert issue.assignee.email == "alice@example.com"  # noqa: S101


async def test_get_issue_returns_none_when_missing() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}}),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearQueries(client).get_issue("nope") is None  # noqa: S101


async def test_unknown_response_fields_are_ignored() -> None:
    """Linear adding a field must not break a pinned SDK version."""
    payload = issue_payload()
    payload["someFieldAddedLater"] = {"nested": True}
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": payload}}),
        )
        async with LinearClient(api_key="key") as client:
            issue = await LinearQueries(client).get_issue("iss-1")
    assert issue is not None  # noqa: S101


async def test_list_issues_page() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [issue_payload("a"), issue_payload("b")],
                            "pageInfo": page_info_payload(has_next=True),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            page = await LinearQueries(client).list_issues_page(
                {
                    "team": {"id": {"eq": "team-1"}},
                    "state": {"type": {"nin": ["completed", "canceled"]}},
                },
                first=100,
                after="previous-page",
                order_by=PaginationOrderBy.updatedAt,
            )

    assert [i.id for i in page.nodes] == ["a", "b"]  # noqa: S101
    assert page.page_info.has_next_page is True  # noqa: S101
    assert page.page_info.end_cursor == "cursor-b"  # noqa: S101

    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {  # noqa: S101
        "filter": {
            "team": {"id": {"eq": "team-1"}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
        "first": 100,
        "after": "previous-page",
        "orderBy": "updatedAt",
        "includeArchived": False,
    }


async def test_list_issues_builds_team_filter() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [issue_payload("a")],
                            "pageInfo": page_info_payload(),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            issues = await LinearQueries(client).list_issues("team-1")

    assert [issue.id for issue in issues] == ["a"]  # noqa: S101
    body = json.loads(route.calls.last.request.content)
    assert body["variables"]["filter"] == {"team": {"id": {"eq": "team-1"}}}  # noqa: S101


async def test_get_team() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"team": {"id": "t1", "name": "Eng", "key": "ENG"}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            team = await LinearQueries(client).get_team("t1")
    assert team is not None  # noqa: S101
    assert team.key == "ENG"  # noqa: S101


async def test_get_team_by_key() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {"nodes": [{"id": "t1", "name": "Eng", "key": "ENG"}]}
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            team = await LinearQueries(client).get_team_by_key("ENG")

    assert team is not None  # noqa: S101
    assert team.id == "t1"  # noqa: S101
    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {"key": "ENG"}  # noqa: S101


async def test_get_team_by_key_returns_none_when_missing() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"teams": {"nodes": []}}}),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearQueries(client).get_team_by_key("NOPE") is None  # noqa: S101


async def test_search_issues() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "searchIssues": {
                            "nodes": [issue_payload()],
                            "pageInfo": page_info_payload(),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            results = await LinearQueries(client).search_issues("hello")

    assert len(results.nodes) == 1  # noqa: S101
    assert results.nodes[0].identifier == "ENG-1"  # noqa: S101


async def test_get_user() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"user": user_payload("u1")}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            user = await LinearQueries(client).get_user("u1")
    assert user is not None  # noqa: S101
    assert user.name == "Alice"  # noqa: S101


async def test_get_viewer() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"viewer": user_payload("me")}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            viewer = await LinearQueries(client).get_viewer()
    assert viewer.id == "me"  # noqa: S101
