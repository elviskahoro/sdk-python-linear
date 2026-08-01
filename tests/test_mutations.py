"""Write-path tests.

The input models are generated from Linear's schema, so field names are snake_case
in Python and camelCase on the wire. The alias round-trip is asserted directly here
because a regression in it would silently break every mutation.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from gtm_linear import (
    IssueCreateInput,
    IssueUpdateInput,
    LinearClient,
    LinearMutations,
)
from tests.conftest import API_URL, issue_payload


def _create_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"issueCreate": {"success": True, "issue": issue_payload()}}},
    )


def _update_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"issueUpdate": {"success": True, "issue": issue_payload()}}},
    )


async def test_create_issue_sends_input_and_parses_response() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            issue = await LinearMutations(client).create_issue(
                IssueCreateInput(title="Hello", team_id="team-1", description="desc"),
            )

    body = json.loads(route.calls.last.request.read())
    # snake_case in Python, camelCase on the wire.
    assert body["variables"]["input"] == {  # noqa: S101
        "title": "Hello",
        "teamId": "team-1",
        "description": "desc",
    }
    assert issue.identifier == "ENG-1"  # noqa: S101


async def test_create_issue_omits_fields_that_were_never_set() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).create_issue(
                IssueCreateInput(title="Hello", team_id="team-1"),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert "description" not in sent  # noqa: S101
    # The generated input carries all 36 schema fields; none of the untouched ones
    # may leak into the request.
    assert set(sent) == {"title", "teamId"}  # noqa: S101


async def test_create_issue_sends_extended_input_fields() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).create_issue(
                IssueCreateInput(
                    title="Hello",
                    team_id="team-1",
                    label_ids=[],
                    priority=0,
                    assignee_id="user-1",
                    project_id="project-1",
                    state_id="state-1",
                ),
            )

    assert json.loads(route.calls.last.request.read())["variables"]["input"] == {  # noqa: S101
        "title": "Hello",
        "teamId": "team-1",
        "labelIds": [],
        "priority": 0,
        "assigneeId": "user-1",
        "projectId": "project-1",
        "stateId": "state-1",
    }


async def test_create_issue_json_serializes_dates_and_enums() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).create_issue(
                IssueCreateInput(
                    title="Hello",
                    team_id="team-1",
                    due_date=date(2026, 8, 1),
                    created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    sla_type="onlyBusinessDays",
                ),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert sent["dueDate"] == "2026-08-01"  # noqa: S101
    assert sent["createdAt"] == "2026-08-01T00:00:00Z"  # noqa: S101
    assert sent["slaType"] == "onlyBusinessDays"  # noqa: S101


async def test_create_issue_raises_when_no_issue_returned() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"issueCreate": {"success": False, "issue": None}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(ValueError, match="did not return an issue"):
                await LinearMutations(client).create_issue(
                    IssueCreateInput(title="x", team_id="t"),
                )


async def test_update_issue() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_update_response())
        async with LinearClient(api_key="key") as client:
            issue = await LinearMutations(client).update_issue(
                "iss-1",
                IssueUpdateInput(title="New title"),
            )

    body = json.loads(route.calls.last.request.read())
    assert body["variables"] == {"id": "iss-1", "input": {"title": "New title"}}  # noqa: S101
    assert issue.id == "iss-1"  # noqa: S101


async def test_update_issue_can_clear_a_field() -> None:
    """Explicit None must reach the API as null, not be dropped.

    This is the case ``exclude_none`` could not express: with it, clearing an
    issue's description was impossible because the field was silently omitted.
    """
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_update_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).update_issue(
                "iss-1",
                IssueUpdateInput(description=None),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert sent == {"description": None}  # noqa: S101


async def test_update_issue_omits_untouched_fields() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_update_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).update_issue(
                "iss-1",
                IssueUpdateInput(title="only this"),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert sent == {"title": "only this"}  # noqa: S101


async def test_delete_issue_returns_success_bool() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"issueDelete": {"success": True}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearMutations(client).delete_issue("iss-1") is True  # noqa: S101


async def test_create_comment() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {
                                "id": "c-1",
                                "body": "hello",
                                "url": "https://linear.app/x/comment/c-1",
                                "createdAt": "2026-01-01T00:00:00.000Z",
                            },
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            comment = await LinearMutations(client).create_comment("iss-1", "hello")

    assert comment.id == "c-1"  # noqa: S101
    # createdAt is DateTime! in the schema, so it parses to a real datetime.
    assert comment.created_at.year == 2026  # noqa: S101
    body = json.loads(route.calls.last.request.read())
    assert body["variables"]["input"] == {"issueId": "iss-1", "body": "hello"}  # noqa: S101
    assert "commentCreate" in body["query"]  # noqa: S101
    assert "createdAt" in body["query"]  # noqa: S101


@pytest.mark.parametrize(
    "comment_payload",
    [None, {"id": "c-1", "body": "hello"}],
    ids=["null-comment", "malformed-comment"],
)
async def test_create_comment_rejects_missing_or_malformed_response(
    comment_payload: object,
) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": comment_payload,
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(ValueError):
                await LinearMutations(client).create_comment("iss-1", "hello")
