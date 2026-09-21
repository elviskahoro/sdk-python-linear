"""Tests for the injected-key :class:`gtm_linear.LinearWorkflow` facade."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
import respx

from gtm_linear import LinearWorkflow, PaginationOrderBy
from gtm_linear.workflow import LinearQueries
from tests.conftest import API_URL, issue_payload, page_info_payload


async def test_async_facade_delegates_every_query_and_mutation(
    monkeypatch: Any,
) -> None:
    """The facade is deliberately thin: every public operation reaches its owner."""
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def query(name: str, result: object) -> Any:
        async def method(_self: object, *args: object, **kwargs: object) -> object:
            calls.append((name, args, kwargs))
            return result

        return method

    def mutation(name: str, result: object) -> Any:
        async def method(_self: object, *args: object, **kwargs: object) -> object:
            calls.append((name, args, kwargs))
            return result

        return method

    from gtm_linear.workflow import LinearMutations

    monkeypatch.setattr(LinearQueries, "get_issue", query("get_issue", "issue"))
    monkeypatch.setattr(LinearQueries, "list_issues", query("list_issues", ["issue"]))
    monkeypatch.setattr(
        LinearQueries,
        "list_issues_page",
        query("list_issues_page", "page"),
    )
    monkeypatch.setattr(
        LinearQueries,
        "list_workflow_states_page",
        query("list_workflow_states_page", "state_page"),
    )
    monkeypatch.setattr(
        LinearQueries,
        "list_workflow_states",
        query("list_workflow_states", ["state"]),
    )
    monkeypatch.setattr(LinearQueries, "get_team", query("get_team", "team"))
    monkeypatch.setattr(
        LinearQueries,
        "get_team_by_key",
        query("get_team_by_key", "team"),
    )
    monkeypatch.setattr(
        LinearQueries,
        "search_issues",
        query("search_issues", "search"),
    )
    monkeypatch.setattr(LinearQueries, "get_user", query("get_user", "user"))
    monkeypatch.setattr(LinearQueries, "get_viewer", query("get_viewer", "viewer"))
    monkeypatch.setattr(
        LinearMutations,
        "create_issue",
        mutation("create_issue", "issue"),
    )
    monkeypatch.setattr(
        LinearMutations,
        "update_issue",
        mutation("update_issue", "issue"),
    )
    monkeypatch.setattr(LinearMutations, "delete_issue", mutation("delete_issue", True))
    monkeypatch.setattr(
        LinearMutations,
        "create_comment",
        mutation("create_comment", "comment"),
    )

    async with LinearWorkflow("key") as linear:
        assert await linear.get_issue_async("i") == "issue"
        assert await linear.list_issues_async("t") == ["issue"]
        assert await linear.list_issues_page_async() == "page"
        assert await linear.list_workflow_states_page_async("t") == "state_page"
        assert await linear.list_workflow_states_async("t") == ["state"]
        assert await linear.get_team_async("t") == "team"
        assert await linear.get_team_by_key_async("ENG") == "team"
        assert await linear.search_issues_async("term") == "search"
        assert await linear.get_user_async("u") == "user"
        assert await linear.get_viewer_async() == "viewer"
        assert await linear.create_issue_async(cast(Any, "create")) == "issue"
        assert await linear.update_issue_async("i", cast(Any, "update")) == "issue"
        assert await linear.delete_issue_async("i") is True
        assert await linear.create_comment_async("i", "body") == "comment"

    assert [name for name, _, _ in calls] == [
        "get_issue",
        "list_issues",
        "list_issues_page",
        "list_workflow_states_page",
        "list_workflow_states",
        "get_team",
        "get_team_by_key",
        "search_issues",
        "get_user",
        "get_viewer",
        "create_issue",
        "update_issue",
        "delete_issue",
        "create_comment",
    ]


def test_sync_facade_uses_injected_key_and_closes_async_session() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "viewer": {
                            "id": "u",
                            "name": "N",
                            "email": "e",
                            "active": True,
                        },
                    },
                },
            ),
        )
        with LinearWorkflow("injected-key") as linear:
            assert linear.get_viewer().id == "u"
            assert linear.client._async_client is None

    body = json.loads(route.calls.last.request.content)
    assert body["query"]
    assert route.calls.last.request.headers["authorization"] == "injected-key"


async def test_list_open_team_issues_applies_workflow_filter_and_order() -> None:
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
        async with LinearWorkflow("key") as linear:
            issues = await linear.list_open_team_issues_async("team-1")

    assert [issue.id for issue in issues] == ["a"]
    variables = json.loads(route.calls.last.request.content)["variables"]
    assert variables["filter"] == {
        "team": {"id": {"eq": "team-1"}},
        "state": {"type": {"nin": ["completed", "canceled"]}},
    }
    assert variables["first"] == 100
    assert variables["orderBy"] == PaginationOrderBy.updatedAt.value


async def test_facade_iterators_delegate_and_sync_iterator_materializes(
    monkeypatch: Any,
) -> None:
    async def issues(
        _self: object,
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[str]:
        yield "one"
        yield "two"

    monkeypatch.setattr(LinearQueries, "iter_issues", issues)
    async with LinearWorkflow("key") as linear:
        assert [item async for item in linear.iter_issues_async()] == ["one", "two"]
        with pytest.raises(RuntimeError, match="asyncio.run"):
            linear.iter_issues()


def test_sync_iterator_materializes_in_one_event_loop(monkeypatch: Any) -> None:
    async def issues(
        _self: object,
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[str]:
        yield "one"
        yield "two"

    monkeypatch.setattr(LinearQueries, "iter_issues", issues)
    assert list(LinearWorkflow("key").iter_issues()) == ["one", "two"]
