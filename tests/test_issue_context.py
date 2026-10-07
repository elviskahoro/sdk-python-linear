"""Typed issue-context reads and cursor pagination."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from gtm_linear import LinearClient, LinearQueries
from tests.conftest import API_URL, page_info_payload, user_payload

if TYPE_CHECKING:
    from collections.abc import Callable


def _comment(comment_id: str) -> dict[str, Any]:
    return {
        "id": comment_id,
        "body": f"Comment {comment_id}",
        "url": f"https://linear.app/acme/comment/{comment_id}",
        "createdAt": "2026-09-01T12:00:00Z",
        "user": user_payload(),
        "externalUser": None,
    }


def _attachment(attachment_id: str) -> dict[str, Any]:
    return {
        "id": attachment_id,
        "title": f"Attachment {attachment_id}",
        "url": f"https://github.com/acme/repo/pull/{attachment_id}",
        "source": {"integration": "github"},
        "sourceType": "github",
        "metadata": {"state": "open"},
        "createdAt": "2026-09-01T12:00:00Z",
    }


def _relation(relation_id: str) -> dict[str, Any]:
    return {
        "id": relation_id,
        "type": "blocks",
        "createdAt": "2026-09-01T12:00:00Z",
        "issue": {
            "id": "issue-source",
            "identifier": "ENG-1",
            "title": "Source issue",
            "url": "https://linear.app/acme/issue/ENG-1",
        },
        "relatedIssue": {
            "id": "issue-related",
            "identifier": "ENG-2",
            "title": "Related issue",
            "url": "https://linear.app/acme/issue/ENG-2",
        },
    }


_CONNECTIONS: list[tuple[str, str, Callable[[str], dict[str, Any]]]] = [
    ("comments", "list_issue_comments_page", _comment),
    ("attachments", "list_issue_attachments_page", _attachment),
    ("relations", "list_issue_relations_page", _relation),
    ("inverseRelations", "list_issue_inverse_relations_page", _relation),
]

_ITERATORS = [
    ("comments", "iter_issue_comments", _comment),
    ("attachments", "iter_issue_attachments", _attachment),
    ("relations", "iter_issue_relations", _relation),
    ("inverseRelations", "iter_issue_inverse_relations", _relation),
]


def _response(
    connection: str,
    nodes: list[dict[str, Any]],
    *,
    has_next: bool = False,
    end: str | None = "cursor-end",
) -> dict[str, Any]:
    return {
        "data": {
            "issue": {
                connection: {
                    "nodes": nodes,
                    "pageInfo": page_info_payload(has_next=has_next, end=end),
                },
            },
        },
    }


@pytest.mark.parametrize(("connection", "method", "make_node"), _CONNECTIONS)
async def test_issue_context_page_exposes_typed_nodes_and_forwards_options(
    connection: str,
    method: str,
    make_node: Callable[[str], dict[str, Any]],
) -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_response(connection, [make_node("node-1")], has_next=True),
            ),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method)(
                "ENG-123",
                first=7,
                after="previous-cursor",
                include_archived=True,
            )

    assert page.nodes[0].id == "node-1"
    assert page.page_info.has_next_page is True
    assert page.page_info.end_cursor == "cursor-end"
    if connection == "comments":
        assert page.nodes[0].body == "Comment node-1"
        assert page.nodes[0].user is not None
        assert page.nodes[0].user.name == "Alice"
    elif connection == "attachments":
        assert page.nodes[0].title == "Attachment node-1"
        assert page.nodes[0].source_type == "github"
        assert page.nodes[0].metadata == {"state": "open"}
    else:
        assert page.nodes[0].type == "blocks"
        assert page.nodes[0].issue.identifier == "ENG-1"
        assert page.nodes[0].issue.title == "Source issue"
        assert page.nodes[0].related_issue.identifier == "ENG-2"
        assert page.nodes[0].related_issue.url.endswith("ENG-2")
    request = json.loads(route.calls.last.request.content)
    assert request["variables"] == {
        "id": "ENG-123",
        "first": 7,
        "after": "previous-cursor",
        "includeArchived": True,
    }


@pytest.mark.parametrize(("connection", "method", "make_node"), _ITERATORS)
async def test_issue_context_iterators_follow_every_page(
    connection: str,
    method: str,
    make_node: Callable[[str], dict[str, Any]],
) -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(
                200,
                json=_response(
                    connection,
                    [make_node("node-1")],
                    has_next=True,
                    end="cursor-1",
                ),
            ),
            httpx.Response(
                200,
                json=_response(
                    connection,
                    [make_node("node-2")],
                    has_next=False,
                    end=None,
                ),
            ),
        ]
        async with LinearClient(api_key="key") as client:
            nodes = [
                node
                async for node in getattr(LinearQueries(client), method)(
                    "ENG-123",
                    page_size=1,
                    include_archived=True,
                )
            ]

    assert [node.id for node in nodes] == ["node-1", "node-2"]
    requests = [json.loads(call.request.content) for call in route.calls]
    assert [request["variables"]["after"] for request in requests] == [
        None,
        "cursor-1",
    ]
    assert all(request["variables"]["includeArchived"] is True for request in requests)


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _CONNECTIONS)
async def test_missing_issue_returns_empty_context_page(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}}),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method)("missing")

    assert page.nodes == []
    assert page.page_info.has_next_page is False
    assert page.page_info.end_cursor is None


@pytest.mark.parametrize(("connection", "method", "_make_node"), _CONNECTIONS)
async def test_empty_issue_context_connection_returns_empty_page(
    connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_response(connection, [])),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method)("ENG-123")

    assert page.nodes == []
    assert page.page_info.has_next_page is False
    request = json.loads(route.calls.last.request.content)
    assert request["variables"]["includeArchived"] is False


async def test_issue_context_validates_malformed_connection_response() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"issue": {"comments": {"pageInfo": {}}}}},
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(ValidationError):
                await LinearQueries(client).list_issue_comments_page("ENG-123")


async def test_issue_context_comment_exposes_external_author() -> None:
    comment = _comment("comment-1")
    comment["user"] = None
    comment["externalUser"] = {
        "id": "external-user-1",
        "displayName": "Reporter",
        "name": "Reporter Name",
        "email": "reporter@example.com",
    }
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_response("comments", [comment])),
        )
        async with LinearClient(api_key="key") as client:
            page = await LinearQueries(client).list_issue_comments_page("ENG-123")

    assert page.nodes[0].user is None
    assert page.nodes[0].external_user is not None
    assert page.nodes[0].external_user.display_name == "Reporter"
