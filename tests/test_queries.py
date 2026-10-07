"""Read-path tests.

``respx.mock`` is used as a context manager rather than a decorator: the decorator is
untyped, so every use needed a ``# type: ignore[misc]``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

import gtm_linear
from gtm_linear import (
    LinearClient,
    LinearQueries,
    LinearWorkflowStateLookupError,
    PaginationOrderBy,
    WorkflowState,
)
from tests.conftest import API_URL, issue_payload, page_info_payload, user_payload


def _workflow_state_page(
    state_ids_and_types: list[tuple[str, str]],
    *,
    has_next: bool,
    end: str | None,
) -> dict[str, object]:
    return {
        "data": {
            "workflowStates": {
                "nodes": [
                    {
                        "id": state_id,
                        "name": state_id.title(),
                        "type": state_type,
                        "color": "#ffffff",
                        "position": float(index),
                    }
                    for index, (state_id, state_type) in enumerate(state_ids_and_types)
                ],
                "pageInfo": page_info_payload(has_next=has_next, end=end),
            },
        },
    }


def test_queries_wildcard_exports_search_result_type() -> None:
    namespace: dict[str, object] = {}
    # exec runs a fixed import-* string to check __all__ exports.
    exec("from gtm_linear.queries import *", namespace)  # noqa: S102  # nosec B102
    assert "IssueSearchResultFields" in namespace


def test_filter_inputs_are_not_public_exports_or_documented_api() -> None:
    obsolete_names = (
        "IssueFilterInput",
        "StringComparatorInput",
        "TeamFilterInput",
        "WorkflowStateFilterInput",
        "WorkflowStateTypeComparatorInput",
        "WorkflowStateType",
    )

    assert not any(hasattr(gtm_linear, name) for name in obsolete_names)
    assert not any(name in gtm_linear.__all__ for name in obsolete_names)
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    assert not any(name in readme for name in obsolete_names)


@pytest.mark.parametrize("archive_mode", ["exclude", "include"])
async def test_get_workflow_state_by_type_returns_unique_match_from_iterator(
    monkeypatch: pytest.MonkeyPatch,
    archive_mode: str,
) -> None:
    include_archived = archive_mode == "include"
    states = [
        WorkflowState(
            id="todo",
            name="Todo",
            type="unstarted",
            color="#ffffff",
            position=1,
        ),
        WorkflowState(
            id="done",
            name="Done",
            type="completed",
            color="#ffffff",
            position=2,
        ),
    ]
    seen: list[tuple[str, dict[str, object]]] = []

    async def iter_states(
        _self: object,
        team_id: str,
        **kwargs: object,
    ):
        seen.append((team_id, kwargs))
        for state in states:
            yield state

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    async with LinearClient(api_key="key") as client:
        state = await LinearQueries(client).get_workflow_state_by_type(
            "team-1",
            "completed",
            include_archived=include_archived,
        )

    assert state.id == "done"
    # ``include_archived`` is the lookup's only iterator option.
    assert seen == [("team-1", {"include_archived": include_archived})]


async def test_get_workflow_state_by_type_accepts_unrecognized_state_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = WorkflowState(
        id="future",
        name="Future",
        type="future-type",
        color="#ffffff",
        position=1,
    )

    async def iter_states(_self: object, _team_id: str, **_kwargs: object):
        yield state

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    async with LinearClient(api_key="key") as client:
        result = await LinearQueries(client).get_workflow_state_by_type(
            "team-1",
            "future-type",
        )

    assert result is state


@pytest.mark.parametrize(
    ("states", "message"),
    [([], "found no state"), (["completed", "completed"], "found multiple states")],
)
async def test_get_workflow_state_by_type_requires_one_match(
    monkeypatch: pytest.MonkeyPatch,
    states: list[str],
    message: str,
) -> None:
    async def iter_states(
        _self: object,
        _team_id: str,
        **_kwargs: object,
    ):
        for index, state_type in enumerate(states):
            yield WorkflowState(
                id=f"state-{index}",
                name=f"State {index}",
                type=state_type,
                color="#ffffff",
                position=index,
            )

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    async with LinearClient(api_key="key") as client:
        with pytest.raises(LinearWorkflowStateLookupError, match=message):
            await LinearQueries(client).get_workflow_state_by_type(
                "team-1",
                "completed",
            )


async def test_get_workflow_state_by_type_finds_match_on_later_page() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(
                200,
                json=_workflow_state_page(
                    [("todo", "unstarted")],
                    has_next=True,
                    end="states-1",
                ),
            ),
            httpx.Response(
                200,
                json=_workflow_state_page(
                    [("done", "completed")],
                    has_next=False,
                    end=None,
                ),
            ),
        ]
        async with LinearClient(api_key="key") as client:
            state = await LinearQueries(client).get_workflow_state_by_type(
                "team-1",
                "completed",
            )

    assert state.id == "done"
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first["variables"]["after"] is None
    assert first["variables"]["includeArchived"] is False
    assert second["variables"]["after"] == "states-1"


async def test_get_workflow_state_by_type_reports_multiple_real_matches() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(
                200,
                json=_workflow_state_page(
                    [("done-a", "completed")],
                    has_next=True,
                    end="states-1",
                ),
            ),
            httpx.Response(
                200,
                json=_workflow_state_page(
                    [("done-b", "completed")],
                    has_next=True,
                    end="states-2",
                ),
            ),
            httpx.Response(
                200,
                json=_workflow_state_page(
                    [("done-c", "completed")],
                    has_next=False,
                    end=None,
                ),
            ),
        ]
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearWorkflowStateLookupError) as error:
                await LinearQueries(client).get_workflow_state_by_type(
                    "team-1",
                    "completed",
                )

    assert error.value.team_id == "team-1"
    assert error.value.state_type == "completed"
    assert error.value.multiple is True
    assert len(route.calls) == 2


async def test_get_workflow_state_by_type_closes_iterator_after_multiple_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    async def iter_states(_self: object, _team_id: str, **_kwargs: object):
        nonlocal closed
        try:
            for index in range(2):
                yield WorkflowState(
                    id=f"done-{index}",
                    name=f"Done {index}",
                    type="completed",
                    color="#ffffff",
                    position=index,
                )
        finally:
            closed = True

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    async with LinearClient(api_key="key") as client:
        with pytest.raises(LinearWorkflowStateLookupError, match="found multiple"):
            await LinearQueries(client).get_workflow_state_by_type(
                "team-1",
                "completed",
            )

    assert closed


def test_workflow_state_lookup_error_direct_construction_has_default_attributes() -> (
    None
):
    error = LinearWorkflowStateLookupError("manual lookup error")

    assert error.team_id is None
    assert error.state_type is None
    assert error.multiple is None


async def test_get_issue_returns_parsed_issue() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": issue_payload()}}),
        )
        async with LinearClient(api_key="key") as client:
            issue = await LinearQueries(client).get_issue("iss-1")

    assert issue is not None
    assert issue.identifier == "ENG-1"
    # `state` is a real object now, not a string flattened from `state { name }`.
    assert issue.state.name == "In Progress"
    assert issue.state.type == "started"
    # Linear's schema types priority as Float!, so the model does too.
    assert issue.priority == 2.0
    assert isinstance(issue.priority, float)
    assert issue.assignee is not None
    assert issue.assignee.email == "alice@example.com"


async def test_get_issue_returns_none_when_missing() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}}),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearQueries(client).get_issue("nope") is None


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
    assert issue is not None


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

    assert [i.id for i in page.nodes] == ["a", "b"]
    assert page.page_info.has_next_page is True
    assert page.page_info.end_cursor == "cursor-b"

    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {
        "filter": {
            "team": {"id": {"eq": "team-1"}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        },
        "first": 100,
        "after": "previous-page",
        "orderBy": "updatedAt",
        "includeArchived": False,
    }


async def test_list_issues_page_accepts_documented_mapping_filter() -> None:
    documented_filter = {
        "team": {"id": {"eq": "team-1"}},
        "state": {"type": {"nin": ["completed", "canceled"]}},
    }
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {"nodes": [], "pageInfo": page_info_payload()},
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            await LinearQueries(client).list_issues_page(documented_filter)

    body = json.loads(route.calls.last.request.content)
    assert body["variables"]["filter"] == documented_filter


async def test_list_issues_page_omits_unset_order_by() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [],
                            "pageInfo": page_info_payload(),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            await LinearQueries(client).list_issues_page({"team": {"id": {"eq": "t1"}}})

    body = json.loads(route.calls.last.request.content)
    assert "orderBy" not in body["variables"]


async def test_list_workflow_states_page_builds_team_filter() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "workflowStates": {
                            "nodes": [
                                {
                                    "id": "state-1",
                                    "name": "In Progress",
                                    "type": "started",
                                    "color": "#f2c94c",
                                    "position": 2.0,
                                },
                            ],
                            "pageInfo": page_info_payload(
                                has_next=True,
                                end="state-cursor",
                            ),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            page = await LinearQueries(client).list_workflow_states_page(
                "team-1",
                first=25,
                after="previous-state-page",
                include_archived=True,
                order_by=PaginationOrderBy.updatedAt,
            )

    assert page.nodes[0].id == "state-1"
    assert page.nodes[0].name == "In Progress"
    assert page.nodes[0].position == 2.0
    assert page.page_info.has_next_page is True
    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {
        "filter": {"team": {"id": {"eq": "team-1"}}},
        "first": 25,
        "after": "previous-state-page",
        "includeArchived": True,
        "orderBy": "updatedAt",
    }


async def test_list_workflow_states_returns_nodes() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "workflowStates": {
                            "nodes": [
                                {
                                    "id": "state-1",
                                    "name": "Todo",
                                    "type": "unstarted",
                                    "color": "#ffffff",
                                    "position": 1.0,
                                },
                            ],
                            "pageInfo": page_info_payload(),
                        },
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            states = await LinearQueries(client).list_workflow_states("team-1")

    assert [state.name for state in states] == ["Todo"]


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

    assert [issue.id for issue in issues] == ["a"]
    body = json.loads(route.calls.last.request.content)
    assert body["variables"]["filter"] == {"team": {"id": {"eq": "team-1"}}}


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
    assert team is not None
    assert team.key == "ENG"


async def test_get_team_by_key() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {"nodes": [{"id": "t1", "name": "Eng", "key": "ENG"}]},
                    },
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            team = await LinearQueries(client).get_team_by_key("ENG")

    assert team is not None
    assert team.id == "t1"
    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {"key": "ENG"}


async def test_get_team_by_key_returns_none_when_missing() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"teams": {"nodes": []}}}),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearQueries(client).get_team_by_key("NOPE") is None


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

    assert len(results.nodes) == 1
    assert results.nodes[0].identifier == "ENG-1"


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
    assert user is not None
    assert user.name == "Alice"


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
    assert viewer.id == "me"
