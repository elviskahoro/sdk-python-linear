import json

import httpx
import pytest
import respx

from src import (
    IssueCreateInput,
    IssueUpdateInput,
    LinearClient,
    LinearMutations,
)

API_URL = LinearClient.BASE_URL


def _issue_payload() -> dict:
    return {
        "id": "iss-1",
        "title": "Hello",
        "description": "desc",
        "identifier": "ENG-1",
        "url": "https://linear.app/x/issue/ENG-1",
        "priority": 0,
        "status": None,
        "assignee": None,
    }


@respx.mock
async def test_create_issue_sends_input_and_parses_response() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"issueCreate": {"success": True, "issue": _issue_payload()}},
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issue = await LinearMutations(client).create_issue(
            IssueCreateInput(title="Hello", teamId="team-1", description="desc"),
        )
    body = json.loads(route.calls.last.request.read())
    assert body["variables"]["input"] == {
        "title": "Hello",
        "teamId": "team-1",
        "description": "desc",
    }
    assert issue.identifier == "ENG-1"


@respx.mock
async def test_create_issue_omits_none_description() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"issueCreate": {"success": True, "issue": _issue_payload()}},
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        await LinearMutations(client).create_issue(
            IssueCreateInput(title="Hello", teamId="team-1"),
        )
    body = json.loads(route.calls.last.request.read())
    assert "description" not in body["variables"]["input"]


@respx.mock
async def test_create_issue_raises_when_no_issue_returned() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"issueCreate": {"success": False, "issue": None}}},
        ),
    )
    async with LinearClient(api_key="key") as client:
        with pytest.raises(ValueError):
            await LinearMutations(client).create_issue(
                IssueCreateInput(title="x", teamId="t"),
            )


@respx.mock
async def test_update_issue() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"issueUpdate": {"success": True, "issue": _issue_payload()}},
            },
        ),
    )
    async with LinearClient(api_key="key") as client:
        issue = await LinearMutations(client).update_issue(
            "iss-1",
            IssueUpdateInput(title="New title"),
        )
    body = json.loads(route.calls.last.request.read())
    assert body["variables"] == {"id": "iss-1", "input": {"title": "New title"}}
    assert issue.id == "iss-1"


@respx.mock
async def test_delete_issue_returns_success_bool() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"issueDelete": {"success": True}}},
        ),
    )
    async with LinearClient(api_key="key") as client:
        assert await LinearMutations(client).delete_issue("iss-1") is True
