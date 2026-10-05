"""Read-path tests.

``respx.mock`` is used as a context manager rather than a decorator: the decorator is
untyped, so every use needed a ``# type: ignore[misc]``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

import gtm_linear
from gtm_linear import (
    LinearClient,
    LinearPaginationError,
    LinearQueries,
    PaginationOrderBy,
)
from tests.conftest import API_URL, issue_payload, page_info_payload, user_payload


def _issue_context_page(
    collection: str,
    nodes: list[dict[str, Any]],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "data": {
            "issue": {
                collection: {
                    "nodes": nodes,
                    "pageInfo": page_info_payload(
                        has_next=has_next,
                        end=end_cursor,
                    ),
                },
            },
        },
    }


def _context_node(collection: str, node_id: str) -> dict[str, Any]:
    if collection == "comments":
        return {
            "id": node_id,
            "body": "Prior discussion",
            "url": f"https://linear.app/x/comment/{node_id}",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "user": user_payload(),
            "externalUser": None,
        }
    if collection == "attachments":
        return {
            "id": node_id,
            "title": "Pull request",
            "url": "https://github.com/org/repo/pull/1",
            "sourceType": "github",
            "source": {"name": "GitHub"},
            "metadata": {"status": "open"},
        }
    return {
        "id": node_id,
        "type": "blocks",
        "relatedIssue": {
            "id": "related-1",
            "identifier": "ENG-2",
            "title": "Related issue",
            "url": "https://linear.app/x/issue/ENG-2",
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


def test_issue_context_models_and_connections_are_public() -> None:
    public_names = (
        "Comment",
        "CommentConnection",
        "Attachment",
        "AttachmentConnection",
        "IssueRelation",
        "IssueRelationConnection",
    )
    assert all(hasattr(gtm_linear, name) for name in public_names)
    assert all(name in gtm_linear.__all__ for name in public_names)


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
    assert set(issue.model_dump()) == {
        "id",
        "identifier",
        "title",
        "description",
        "url",
        "priority",
        "state",
        "assignee",
    }


async def test_get_issue_returns_none_when_missing() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}}),
        )
        async with LinearClient(api_key="key") as client:
            assert await LinearQueries(client).get_issue("nope") is None


@pytest.mark.parametrize(
    ("collection", "method_name"),
    [
        ("comments", "list_issue_comments_page"),
        ("attachments", "list_issue_attachments_page"),
        ("relations", "list_issue_relations_page"),
    ],
)
async def test_issue_context_page_returns_typed_nodes_and_cursor(
    collection: str,
    method_name: str,
) -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_issue_context_page(
                    collection,
                    [_context_node(collection, "node-1")],
                    has_next=True,
                    end_cursor="next-cursor",
                ),
            ),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method_name)(
                "ENG-1",
                first=10,
                after="previous-cursor",
                order_by=PaginationOrderBy.updatedAt,
                include_archived=True,
            )

    assert page.nodes[0].id == "node-1"
    assert page.page_info.has_next_page is True
    assert page.page_info.end_cursor == "next-cursor"
    request = json.loads(route.calls.last.request.content)
    assert request["variables"] == {
        "id": "ENG-1",
        "first": 10,
        "after": "previous-cursor",
        "includeArchived": True,
        "orderBy": "updatedAt",
    }

    node = page.nodes[0]
    if collection == "comments":
        assert node.body == "Prior discussion"
        assert node.user is not None
        assert node.user.name == "Alice"
        assert node.external_user is None
    elif collection == "attachments":
        assert node.title == "Pull request"
        assert node.source_type == "github"
        assert node.source == {"name": "GitHub"}
        assert node.metadata == {"status": "open"}
    else:
        assert node.type == "blocks"
        assert node.related_issue.identifier == "ENG-2"
        assert node.related_issue.url == "https://linear.app/x/issue/ENG-2"


@pytest.mark.parametrize(
    ("collection", "method_name"),
    [
        ("comments", "list_issue_comments_page"),
        ("attachments", "list_issue_attachments_page"),
        ("relations", "list_issue_relations_page"),
    ],
)
async def test_issue_context_page_accepts_empty_collection(
    collection: str,
    method_name: str,
) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_issue_context_page(collection, []),
            ),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method_name)("ENG-1")

    assert page.nodes == []
    assert page.page_info.has_next_page is False


@pytest.mark.parametrize(
    ("collection", "method_name"),
    [
        ("comments", "list_issue_comments_page"),
        ("attachments", "list_issue_attachments_page"),
        ("relations", "list_issue_relations_page"),
    ],
)
async def test_issue_context_page_validates_node_payload(
    collection: str,
    method_name: str,
) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_issue_context_page(collection, [{}]),
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(ValidationError):
                await getattr(LinearQueries(client), method_name)("ENG-1")


@pytest.mark.parametrize(
    ("collection", "iterator_name"),
    [
        ("comments", "iter_issue_comments"),
        ("attachments", "iter_issue_attachments"),
        ("relations", "iter_issue_relations"),
    ],
)
async def test_issue_context_iterators_follow_cursors(
    collection: str,
    iterator_name: str,
) -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(
                200,
                json=_issue_context_page(
                    collection,
                    [_context_node(collection, "node-1")],
                    has_next=True,
                    end_cursor="cursor-1",
                ),
            ),
            httpx.Response(
                200,
                json=_issue_context_page(
                    collection,
                    [_context_node(collection, "node-2")],
                ),
            ),
        ]
        async with LinearClient(api_key="key") as client:
            nodes = [
                node
                async for node in getattr(LinearQueries(client), iterator_name)(
                    "ENG-1",
                    page_size=1,
                )
            ]

    assert [node.id for node in nodes] == ["node-1", "node-2"]
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first["variables"]["after"] is None
    assert second["variables"]["after"] == "cursor-1"


@pytest.mark.parametrize(
    ("collection", "iterator_name"),
    [
        ("comments", "iter_issue_comments"),
        ("attachments", "iter_issue_attachments"),
        ("relations", "iter_issue_relations"),
    ],
)
async def test_issue_context_iterators_raise_for_missing_continuation_cursor(
    collection: str,
    iterator_name: str,
) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_issue_context_page(
                    collection,
                    [_context_node(collection, "node-1")],
                    has_next=True,
                    end_cursor=None,
                ),
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearPaginationError):
                [
                    node
                    async for node in getattr(LinearQueries(client), iterator_name)(
                        "ENG-1",
                    )
                ]


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
