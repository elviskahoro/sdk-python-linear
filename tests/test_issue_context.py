"""Typed issue-context reads and cursor pagination."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
import pytest
import respx
from pydantic import ValidationError

from gtm_linear import LinearClient, LinearQueries
from gtm_linear.exceptions import LinearGraphQLError
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


# The errors-array not-found shape Linear returns for unknown issue
# identifiers. Captured verbatim from Linear's live API (2026-10-02) — the
# same pinned payload ``tests/test_queries.py`` and ``tests/test_cli.py`` use
# for the ``get_issue`` / CLI not-found paths. Linear sends a top-level
# ``errors`` array with no ``data`` key, so ``LinearClient._handle_response``
# raises before the page methods' null-issue guard can run.
_ISSUE_NOT_FOUND_ERRORS_PAYLOAD: dict[str, Any] = {
    "errors": [
        {
            "message": "Entity not found: Issue",
            "extensions": {
                "type": "invalid input",
                "code": "INPUT_ERROR",
                "statusCode": 400,
                "userError": True,
                "userPresentableMessage": "Could not find referenced Issue.",
            },
        },
    ],
}

# A reworded top-level message whose presentable text still names the Issue
# entity (mirrors ``tests/test_cli.py``'s extension-wording capture).
_REWORDED_NOT_FOUND_ERRORS_PAYLOAD: dict[str, Any] = {
    "errors": [
        {
            "message": "Look-up failed",
            "extensions": {
                "userPresentableMessage": "Could not find referenced Issue.",
            },
        },
    ],
}


def _assert_empty_page(page: Any) -> None:
    assert page.nodes == []
    assert page.page_info.has_next_page is False
    assert page.page_info.end_cursor is None


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _CONNECTIONS)
async def test_issue_context_page_returns_empty_page_for_errors_array_not_found(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    """The real Linear not-found shape (top-level errors array) yields an empty page.

    Mirrors ``tests/test_queries.py::test_get_issue_returns_none_for_not_found_identifier``:
    Linear reports unknown identifiers as a GraphQL ``errors`` array with no
    ``data`` key, and ``LinearClient._handle_response`` raises on it, so the
    page methods must translate an issue-not-found into the documented valid
    empty page rather than propagate ``LinearGraphQLError``. Before the fix
    the null-``issue`` guard was dead code for this shape and the methods
    raised instead of honoring the empty-page contract.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_ISSUE_NOT_FOUND_ERRORS_PAYLOAD),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method)("ENG-404")
    _assert_empty_page(page)


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _CONNECTIONS)
async def test_issue_context_page_returns_empty_page_for_reworded_not_found(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    """A reworded message still matches through ``userPresentableMessage``.

    Mirrors ``tests/test_queries.py::test_get_issue_returns_none_for_reworded_not_found_via_user_presentable_message``:
    the message text is the one Linear may rewrite, but the presentable
    message still names the Issue entity, so the not-found contract still
    holds for all four context-page methods.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_REWORDED_NOT_FOUND_ERRORS_PAYLOAD),
        )
        async with LinearClient(api_key="key") as client:
            page = await getattr(LinearQueries(client), method)("ENG-404")
    _assert_empty_page(page)


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _CONNECTIONS)
async def test_issue_context_page_re_raises_for_other_entity_not_found(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    """A not-found for a different referenced entity is NOT an issue not-found.

    Mirrors ``tests/test_queries.py::test_get_issue_re_raises_for_other_entity_not_found``:
    the wording heuristic names the Issue entity specifically, so a
    referenced-team not-found still propagates as ``LinearGraphQLError``
    instead of being silenced into an empty page. Without this guard every
    ``LinearGraphQLError`` would be swallowed and callers could not
    distinguish issue-not-found from genuine failures.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"errors": [{"message": "Entity not found: Team"}]},
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearGraphQLError):
                await getattr(LinearQueries(client), method)("ENG-1")


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _CONNECTIONS)
async def test_issue_context_page_re_raises_unrelated_api_errors(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    """Unrelated GraphQL errors (auth, rate limit) still propagate.

    Mirrors ``tests/test_queries.py::test_get_issue_re_raises_for_unrelated_api_errors``:
    a payload that does not mention the Issue not-found wording escapes
    untouched, so callers see real failures as exceptions rather than a
    silently empty page.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"errors": [{"message": "boom"}]},
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearGraphQLError):
                await getattr(LinearQueries(client), method)("ENG-1")


@pytest.mark.parametrize(("_connection", "method", "_make_node"), _ITERATORS)
async def test_issue_context_iterators_yield_nothing_for_errors_array_not_found(
    _connection: str,
    method: str,
    _make_node: Callable[[str], dict[str, Any]],
) -> None:
    """The ``iter_issue_*`` wrappers yield zero items for the real not-found shape.

    ``paginate`` calls the page method, which now returns an empty page for
    the top-level ``errors`` array shape (``hasNextPage`` false, no cursor),
    so iteration ends after one round trip yielding nothing instead of
    raising ``LinearGraphQLError`` out of the async generator. Before the
    fix the exception propagated through the wrapper.
    """
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json=_ISSUE_NOT_FOUND_ERRORS_PAYLOAD),
        )
        async with LinearClient(api_key="key") as client:
            nodes = [
                node async for node in getattr(LinearQueries(client), method)("ENG-404")
            ]
    assert nodes == []
    # The wrapper must stop after the single not-found round trip — no
    # refetch loop, no stall.
    assert len(route.calls) == 1


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
