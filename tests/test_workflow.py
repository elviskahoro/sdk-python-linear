"""Tests for the injected-key :class:`gtm_linear.LinearWorkflow` facade."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
import respx

from gtm_linear import (
    AttachmentCreateInput,
    IssueRelationCreateInput,
    IssueRelationType,
    LinearClient,
    LinearWorkflow,
    PaginationOrderBy,
    WorkflowState,
)
from gtm_linear.mutations import LinearMutations
from gtm_linear.queries import LinearQueries
from tests.conftest import API_URL, issue_payload, page_info_payload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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

    async def workflow_states(
        _self: object,
        *args: object,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        calls.append(("iter_workflow_states", args, kwargs))
        yield "state"

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
    monkeypatch.setattr(
        LinearQueries,
        "iter_workflow_states",
        workflow_states,
    )
    monkeypatch.setattr(
        LinearQueries,
        "get_workflow_state_by_type",
        query("get_workflow_state_by_type", "state"),
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
    monkeypatch.setattr(
        LinearMutations,
        "create_attachment",
        mutation("create_attachment", "attachment"),
    )
    monkeypatch.setattr(
        LinearMutations,
        "create_issue_relation",
        mutation("create_issue_relation", "relation"),
    )

    async with LinearWorkflow("key") as linear:
        assert await linear.get_issue_async("i") == "issue"
        assert await linear.list_issues_async("t") == ["issue"]
        assert await linear.list_issues_page_async() == "page"
        assert await linear.list_workflow_states_page_async("t") == "state_page"
        assert await linear.list_workflow_states_async("t") == ["state"]
        assert [
            state
            async for state in linear.iter_workflow_states_async("t", page_size=10)
        ] == ["state"]
        assert (
            await linear.get_workflow_state_by_type_async("t", "completed")
            == "state"
        )
        assert await linear.get_team_async("t") == "team"
        assert await linear.get_team_by_key_async("ENG") == "team"
        assert await linear.search_issues_async("term") == "search"
        assert await linear.get_user_async("u") == "user"
        assert await linear.get_viewer_async() == "viewer"
        assert await linear.create_issue_async(cast("Any", "create")) == "issue"
        assert await linear.update_issue_async("i", cast("Any", "update")) == "issue"
        assert await linear.delete_issue_async("i") is True
        assert await linear.create_comment_async("i", "body") == "comment"
        assert (
            await linear.create_attachment_async(
                cast(
                    "Any",
                    AttachmentCreateInput(
                        issue_id="ENG-123",
                        url="https://example.com/pr/1",
                        title="PR",
                    ),
                ),
            )
            == "attachment"
        )
        assert (
            await linear.create_issue_relation_async(
                cast(
                    "Any",
                    IssueRelationCreateInput(
                        issue_id="ENG-123",
                        related_issue_id="ENG-124",
                        type=IssueRelationType.related,
                    ),
                ),
            )
            == "relation"
        )

    assert [name for name, _, _ in calls] == [
        "get_issue",
        "list_issues",
        "list_issues_page",
        "list_workflow_states_page",
        "list_workflow_states",
        "iter_workflow_states",
        "get_workflow_state_by_type",
        "get_team",
        "get_team_by_key",
        "search_issues",
        "get_user",
        "get_viewer",
        "create_issue",
        "update_issue",
        "delete_issue",
        "create_comment",
        "create_attachment",
        "create_issue_relation",
    ]


def test_sync_wrappers_inherit_docstrings_without_wraps_binding() -> None:
    """Sync wrappers copy ``__doc__`` from their async source — nothing else.

    The obvious implementation is ``@functools.wraps(source)``, but wraps also
    sets ``__wrapped__``, and pyright resolves calls to a wrapped method through
    the source's *unbound* signature: the instance never satisfies ``self`` and
    every correct call reports "Argument missing for parameter ...". gtm-sdk
    carried six ``# pyright: ignore[reportCallIssue]`` suppressions for exactly
    that (elviskahoro/gtm-sdk#848). This pins the contract the doc-only
    ``_sync_doc`` decorator provides: same docstring, no ``__wrapped__``, and a
    qualname that still attributes the method to ``LinearWorkflow``.
    """
    pairs = [
        (LinearWorkflow.get_issue, LinearQueries.get_issue),
        (LinearWorkflow.list_issues, LinearQueries.list_issues),
        (LinearWorkflow.list_issues_page, LinearQueries.list_issues_page),
        (
            LinearWorkflow.list_workflow_states,
            LinearQueries.list_workflow_states,
        ),
        (
            LinearWorkflow.list_workflow_states_page,
            LinearQueries.list_workflow_states_page,
        ),
        (
            LinearWorkflow.get_workflow_state_by_type,
            LinearQueries.get_workflow_state_by_type,
        ),
        (LinearWorkflow.get_team, LinearQueries.get_team),
        (LinearWorkflow.get_team_by_key, LinearQueries.get_team_by_key),
        (LinearWorkflow.search_issues, LinearQueries.search_issues),
        (LinearWorkflow.get_user, LinearQueries.get_user),
        (LinearWorkflow.get_viewer, LinearQueries.get_viewer),
        (LinearWorkflow.create_issue, LinearMutations.create_issue),
        (LinearWorkflow.update_issue, LinearMutations.update_issue),
        (LinearWorkflow.delete_issue, LinearMutations.delete_issue),
        (LinearWorkflow.create_comment, LinearMutations.create_comment),
        (LinearWorkflow.create_attachment, LinearMutations.create_attachment),
        (LinearWorkflow.create_issue_relation, LinearMutations.create_issue_relation),
    ]
    for sync, source in pairs:
        assert sync.__doc__ == source.__doc__, sync.__name__
        assert not hasattr(sync, "__wrapped__"), sync.__name__
        assert sync.__qualname__.startswith("LinearWorkflow."), sync.__qualname__


def test_sync_workflow_state_iterator_documents_materialization() -> None:
    sync_iterator = LinearWorkflow.iter_workflow_states

    assert sync_iterator.__doc__ == (
        "Sync iterator over all states; materializes every page before yielding."
    )
    assert not hasattr(sync_iterator, "__wrapped__")


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


def test_sync_attachment_and_relation_methods_delegate(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, object]] = []

    async def create_attachment(_self: object, input_: object) -> str:
        calls.append(("create_attachment", input_))
        return "attachment"

    async def create_issue_relation(_self: object, input_: object) -> str:
        calls.append(("create_issue_relation", input_))
        return "relation"

    monkeypatch.setattr(LinearMutations, "create_attachment", create_attachment)
    monkeypatch.setattr(LinearMutations, "create_issue_relation", create_issue_relation)
    attachment_input = AttachmentCreateInput(
        issue_id="ENG-123",
        url="https://example.com/pr/1",
        title="PR",
    )
    relation_input = IssueRelationCreateInput(
        issue_id="ENG-123",
        related_issue_id="ENG-124",
        type=IssueRelationType.related,
    )

    linear = LinearWorkflow("key")
    assert linear.create_attachment(attachment_input) == "attachment"
    assert linear.create_issue_relation(relation_input) == "relation"
    assert calls == [
        ("create_attachment", attachment_input),
        ("create_issue_relation", relation_input),
    ]


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


async def test_workflow_state_facade_delegates_async_lookup_and_iterator(
    monkeypatch: Any,
) -> None:
    state = WorkflowState(
        id="done",
        name="Done",
        type="completed",
        color="#ffffff",
        position=1,
    )
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def iter_states(
        _self: object,
        *args: object,
        **kwargs: object,
    ) -> AsyncIterator[WorkflowState]:
        calls.append(("iter", args, kwargs))
        yield state

    async def get_state(
        _self: object,
        *args: object,
        **kwargs: object,
    ) -> WorkflowState:
        calls.append(("get", args, kwargs))
        return state

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    monkeypatch.setattr(LinearQueries, "get_workflow_state_by_type", get_state)
    async with LinearWorkflow("key") as linear:
        assert [
            item
            async for item in linear.iter_workflow_states_async(
                "team-1",
                page_size=10,
                include_archived=True,
            )
        ] == [state]
        assert (
            await linear.get_workflow_state_by_type_async(
                "team-1",
                "completed",
                include_archived=True,
            )
            == state
        )

    assert calls == [
        (
            "iter",
            ("team-1",),
            {
                "page_size": 10,
                "limit": None,
                "include_archived": True,
                "order_by": None,
            },
        ),
        (
            "get",
            ("team-1", "completed"),
            {"include_archived": True},
        ),
    ]


def test_workflow_state_sync_wrappers_materialize_and_resolve(
    monkeypatch: Any,
) -> None:
    state = WorkflowState(
        id="done",
        name="Done",
        type="completed",
        color="#ffffff",
        position=1,
    )

    async def iter_states(
        _self: object,
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[WorkflowState]:
        yield state

    async def get_state(
        _self: object,
        *_args: object,
        **_kwargs: object,
    ) -> WorkflowState:
        return state

    monkeypatch.setattr(LinearQueries, "iter_workflow_states", iter_states)
    monkeypatch.setattr(LinearQueries, "get_workflow_state_by_type", get_state)
    linear = LinearWorkflow("key")
    assert list(linear.iter_workflow_states("team-1")) == [state]
    assert linear.get_workflow_state_by_type("team-1", "completed") == state


async def test_sync_method_in_running_loop_emits_no_coroutine_warning(
    monkeypatch: Any,
) -> None:
    """``_run`` closes its unstarted coroutine before raising RuntimeError.

    Without the defensive ``coroutine.close()`` call inside the running-loop
    branch, Python leaks a ``coroutine '...' was never awaited`` RuntimeWarning
    alongside the RuntimeError. Users who misuse a sync wrapper inside an
    async workflow should see one actionable error, not error-plus-warning
    noise.
    """

    async def fake(_self: object, *_args: object, **_kwargs: object) -> str:
        return "issue"

    monkeypatch.setattr(LinearQueries, "get_issue", fake)

    async with LinearWorkflow("key") as linear:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="asyncio.run"):
                linear.get_issue("i")

    coroutine_warnings = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    assert coroutine_warnings == [], (
        "Expected no 'coroutine was never awaited' warning; got: "
        f"{[str(w.message) for w in coroutine_warnings]}"
    )


def test_workflow_rejects_a_client_instance_where_the_key_belongs() -> None:
    """Regression from an automation run: ``LinearWorkflow(LinearClient(...))``.

    The facade forwards its first argument to ``LinearClient(api_key=...)``.
    Before the constructor guard, the client instance was silently stored as
    the key and the failure surfaced only at the first request, as
    ``TypeError: Header value must be str or bytes, not LinearClient`` deep
    inside httpx. LinearQueries/LinearMutations take a client; LinearWorkflow
    takes the key.
    """
    with pytest.raises(TypeError, match="not LinearClient"):
        LinearWorkflow(cast("Any", LinearClient(api_key="key")))


def test_workflow_strips_a_padded_key() -> None:
    """The facade forwards its key argument through the same constructor guard.

    ``LinearWorkflow`` only works by forwarding to ``LinearClient.__init__``;
    this pins that forwarding so a later bypass is caught.
    """
    linear = LinearWorkflow("lin_api_x\n")
    assert linear.client.api_key.get_secret_value() == "lin_api_x"
