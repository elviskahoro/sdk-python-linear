import json
from typing import Any

import httpx
import respx

from gtm_linear import (
    IssueFilterInput,
    LinearClient,
    LinearQueries,
    PaginationOrderBy,
    StringComparatorInput,
    TeamFilterInput,
    WorkflowStateFilterInput,
    WorkflowStateType,
    WorkflowStateTypeComparatorInput,
)
from gtm_linear.client import LinearClient as ClientCls

API_URL = ClientCls.BASE_URL


def _issue_payload(issue_id: str = "iss-1") -> dict[str, Any]:
    return {
        "id": issue_id,
        "title": "Hello",
        "description": "desc",
        "identifier": "ENG-1",
        "url": "https://linear.app/x/issue/ENG-1",
        "priority": 2,
        "status": {"name": "In Progress"},
        "assignee": {
            "id": "u-1",
            "name": "Alice",
            "email": "alice@example.com",
            "active": True,
        },
    }


@respx.mock  # type: ignore[misc]
async def test_get_issue_returns_parsed_issue() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"issue": _issue_payload()}}),
    )
    async with LinearClient(api_key="key") as client:
        issue = await LinearQueries(client).get_issue("iss-1")
    assert issue is not None  # noqa: S101
    assert issue.identifier == "ENG-1"  # noqa: S101
    assert issue.status == "In Progress"  # noqa: S101
    assert issue.assignee is not None  # noqa: S101
    assert issue.assignee.email == "alice@example.com"  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_get_issue_returns_none_when_missing() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"issue": None}}),
    )
    async with LinearClient(api_key="key") as client:
        assert await LinearQueries(client).get_issue("nope") is None  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_list_issues_page() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_issue_payload("a"), _issue_payload("b")],
                        "pageInfo": {
                            "hasNextPage": True,
                            "hasPreviousPage": False,
                            "startCursor": "cursor-a",
                            "endCursor": "cursor-b",
                        },
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issues = await LinearQueries(client).list_issues_page(
            IssueFilterInput(  # type: ignore[call-arg]
                team=TeamFilterInput(  # type: ignore[call-arg]
                    id=StringComparatorInput(eq="team-1"),  # type: ignore[call-arg]
                ),
                state=WorkflowStateFilterInput(  # type: ignore[call-arg]
                    type=WorkflowStateTypeComparatorInput(  # type: ignore[call-arg]
                        nin=[  # pyright: ignore[reportCallIssue]
                            WorkflowStateType.COMPLETED,
                            WorkflowStateType.CANCELED,
                        ],
                    ),
                ),
            ),
            first=100,
            after="previous-page",
            order_by=PaginationOrderBy.UPDATED_AT,
        )
    assert [i.id for i in issues.nodes] == ["a", "b"]  # noqa: S101
    assert issues.pageInfo.hasNextPage is True  # noqa: S101
    assert issues.pageInfo.endCursor == "cursor-b"  # noqa: S101
    request_body = json.loads(route.calls.last.request.content)
    assert request_body["variables"] == {  # noqa: S101
        "filter": {
            "team": {"id": {"eq": "team-1"}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
        "first": 100,
        "after": "previous-page",
        "orderBy": "updatedAt",
        "includeArchived": False,
    }


@respx.mock  # type: ignore[misc]
async def test_list_issues_supports_state_inclusion_and_follow_up_cursor() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_issue_payload("b")],
                        "pageInfo": {
                            "hasNextPage": False,
                            "hasPreviousPage": True,
                            "startCursor": "cursor-b",
                            "endCursor": "cursor-b",
                        },
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issues = await LinearQueries(client).list_issues_page(
            IssueFilterInput(  # type: ignore[call-arg]
                state=WorkflowStateFilterInput(  # type: ignore[call-arg]
                    type=WorkflowStateTypeComparatorInput(  # type: ignore[call-arg]
                        in_=[WorkflowStateType.STARTED],  # pyright: ignore[reportCallIssue]
                    ),
                ),
            ),
            after="cursor-a",
            include_archived=True,
        )
    assert issues.pageInfo.hasPreviousPage is True  # noqa: S101
    assert issues.pageInfo.hasNextPage is False  # noqa: S101
    request_body = json.loads(route.calls.last.request.content)
    assert request_body["variables"]["filter"] == {  # noqa: S101
        "state": {"type": {"in": ["started"]}},
    }
    assert request_body["variables"]["after"] == "cursor-a"  # noqa: S101
    assert request_body["variables"]["includeArchived"] is True  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_list_issues_preserves_legacy_list_api() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_issue_payload("a")],
                        "pageInfo": {
                            "hasNextPage": False,
                            "hasPreviousPage": False,
                            "startCursor": "cursor-a",
                            "endCursor": "cursor-a",
                        },
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issues = await LinearQueries(client).list_issues("team-1")
    assert [issue.id for issue in issues] == ["a"]  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_get_team() -> None:
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


@respx.mock  # type: ignore[misc]
async def test_get_team_by_key() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": [{"id": "t1", "name": "Eng", "key": "ENG"}],
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        team = await LinearQueries(client).get_team_by_key("ENG")
    assert team is not None  # noqa: S101
    assert team.id == "t1"  # noqa: S101
    request_body = json.loads(route.calls.last.request.content)
    assert request_body["variables"] == {"key": "ENG"}  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_get_team_by_key_returns_none_when_missing() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"teams": {"nodes": []}}}),
    )
    async with LinearClient(api_key="key") as client:
        assert await LinearQueries(client).get_team_by_key("NOPE") is None  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_search_issues() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"searchIssues": {"nodes": [_issue_payload()]}}},
        ),
    )
    async with LinearClient(api_key="key") as client:
        results = await LinearQueries(client).search_issues("hello")
    assert len(results) == 1  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_get_user() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "user": {
                        "id": "u1",
                        "name": "Bob",
                        "email": "b@x.com",
                        "active": True,
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        user = await LinearQueries(client).get_user("u1")
    assert user is not None  # noqa: S101
    assert user.name == "Bob"  # noqa: S101
