"""Write-path tests.

The input models are generated from Linear's schema, so field names are snake_case
in Python and camelCase on the wire. The alias round-trip is asserted directly here
because a regression in it would silently break every mutation.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from pydantic import ValidationError

from gtm_linear import (
    IssueCreateInput,
    IssueUpdateInput,
    LinearClient,
    LinearMutations,
)
from gtm_linear._generated.CreateIssue import SLADayCountType
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
    assert body["variables"]["input"] == {
        "title": "Hello",
        "teamId": "team-1",
        "description": "desc",
    }
    assert issue.identifier == "ENG-1"


async def test_create_issue_omits_fields_that_were_never_set() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).create_issue(
                IssueCreateInput(title="Hello", team_id="team-1"),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert "description" not in sent
    # The generated input carries all 36 schema fields; none of the untouched ones
    # may leak into the request.
    assert set(sent) == {"title", "teamId"}


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

    assert json.loads(route.calls.last.request.read())["variables"]["input"] == {
        "title": "Hello",
        "teamId": "team-1",
        "labelIds": [],
        "priority": 0,
        "assigneeId": "user-1",
        "projectId": "project-1",
        "stateId": "state-1",
    }


@pytest.mark.parametrize(
    "sla_type",
    [SLADayCountType.onlyBusinessDays, "onlyBusinessDays"],
)
async def test_create_issue_json_serializes_dates_and_enums(
    sla_type: SLADayCountType | str,
) -> None:
    """The enum member is the typed path; the raw string stays covered because
    pydantic coerces valid enum values by name, and callers do pass strings.
    """
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_create_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).create_issue(
                IssueCreateInput(
                    title="Hello",
                    team_id="team-1",
                    due_date=date(2026, 8, 1),
                    created_at=datetime(2026, 8, 1, tzinfo=UTC),
                    sla_type=sla_type,
                ),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert sent["dueDate"] == "2026-08-01"
    assert sent["createdAt"] == "2026-08-01T00:00:00Z"
    assert sent["slaType"] == "onlyBusinessDays"


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
    assert body["variables"] == {"id": "iss-1", "input": {"title": "New title"}}
    assert issue.id == "iss-1"


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
    assert sent == {"description": None}


async def test_update_issue_omits_untouched_fields() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=_update_response())
        async with LinearClient(api_key="key") as client:
            await LinearMutations(client).update_issue(
                "iss-1",
                IssueUpdateInput(title="only this"),
            )

    sent = json.loads(route.calls.last.request.read())["variables"]["input"]
    assert sent == {"title": "only this"}


async def test_delete_issue_returns_success_bool() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"issueDelete": {"success": True}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearMutations(client).delete_issue("iss-1") is True


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

    assert comment.id == "c-1"
    # createdAt is DateTime! in the schema, so it parses to a real datetime.
    assert comment.created_at.year == 2026
    body = json.loads(route.calls.last.request.read())
    assert body["variables"]["input"] == {"issueId": "iss-1", "body": "hello"}
    assert "commentCreate" in body["query"]
    assert "createdAt" in body["query"]


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


async def test_create_comment_null_payload_fails_validation_not_a_silent_none() -> None:
    """``CommentPayload.comment`` is ``Comment!`` in Linear's schema, unlike
    ``IssuePayload.issue`` (nullable, guarded in create_issue). A null comment
    is therefore a schema violation by the server and must fail model
    validation loudly — never parse to ``None`` and flow onward as a None
    return. This pins that asymmetry as deliberate: it mirrors Linear's SDL,
    not an oversight.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"commentCreate": {"success": True, "comment": None}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(ValidationError):
                await LinearMutations(client).create_comment("iss-1", "hello")
