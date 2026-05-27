from typing import Any

import httpx
import respx

from gtm_linear import LinearClient, LinearQueries
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
async def test_list_issues() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "team": {
                        "issues": {
                            "nodes": [_issue_payload("a"), _issue_payload("b")],
                        },
                    },
                },
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issues = await LinearQueries(client).list_issues("team-1")
    assert [i.id for i in issues] == ["a", "b"]  # noqa: S101


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
