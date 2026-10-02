"""CLI-friendly workflow facade over the typed Linear query and mutation APIs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any, Self, TypeVar

from ._generated.ListIssues import PaginationOrderBy
from .client import DEFAULT_TIMEOUT, LinearClient
from .mutations import LinearMutations
from .queries import LinearQueries

if TYPE_CHECKING:
    from pydantic import SecretStr

    from ._generated.CreateIssue import IssueCreateInput
    from ._generated.ListIssues import ListIssuesResultIssues
    from ._generated.ListWorkflowStates import ListWorkflowStatesResultWorkflowStates
    from ._generated.SearchIssues import SearchIssuesResultSearchIssues
    from ._generated.UpdateIssue import IssueUpdateInput
    from ._generated.fragments import (
        CommentFields,
        IssueFields,
        IssueSearchResultFields,
        TeamFields,
        UserFields,
        WorkflowStateFields,
    )


T = TypeVar("T")

# Identity-decorator TypeVar for _sync_doc: binding to the wrapper's own type
# is what keeps the sync method's real signature visible to type checkers.
_F = TypeVar("_F", bound=Callable[..., object])


def _sync_doc(source: Callable[..., object]) -> Callable[[_F], _F]:
    """Copy the async source method's docstring onto a sync wrapper.

    ``functools.wraps(source)`` looks like the obvious tool here, but it also
    sets ``__wrapped__`` — and pyright then resolves every call through the
    source's *unbound* signature, so the instance never satisfies ``self`` and
    each correct ``workflow.<method>(...)`` call is flagged "Argument missing
    for parameter ..." (six such false ``reportCallIssue`` findings landed on
    gtm-sdk's main; see elviskahoro/gtm-sdk#848). Copying only ``__doc__``
    keeps ``help()`` output identical while the wrapper keeps its own — bound
    and correct — signature. It also stops ``wraps`` from stamping the source's
    ``__qualname__`` over the wrapper's, which misattributed the workflow
    methods to ``LinearQueries``/``LinearMutations`` in tracebacks.
    """

    def decorate(wrapper: _F) -> _F:
        wrapper.__doc__ = source.__doc__
        return wrapper

    return decorate


class LinearWorkflow:
    """A single injected-key facade for Linear CLI and automation workflows.

    The facade owns one :class:`LinearClient` and exposes the SDK's typed read and
    write operations as paired asynchronous and synchronous methods. Use it as a
    context manager when a workflow makes more than one call::

        with LinearWorkflow("lin_api_...") as linear:
            team = linear.get_team_by_key("ENG")
            assert team is not None
            issue = linear.create_issue(
                IssueCreateInput(title="Triage", team_id=team.id)
            )

    The ``*_async`` methods are for async applications and should normally be used
    under ``async with``. Synchronous methods use :func:`asyncio.run`, so Python
    raises its usual ``RuntimeError`` if they are called from an active event loop.

    Note: Chaining multiple synchronous calls inside a single ``with`` block does not
    pool HTTP connections because each call spins up its own temporary event loop and
    ``httpx.AsyncClient``. For performance-sensitive workflows making many calls, use
    the ``*_async`` methods.
    """

    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        base_url: str | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> None:
        """Build the facade from the same inputs :class:`LinearClient` takes."""
        self._client = LinearClient(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._queries = LinearQueries(self._client)
        self._mutations = LinearMutations(self._client)

    @property
    def client(self) -> LinearClient:
        """The owned client for callers needing the raw GraphQL escape hatch."""
        return self._client

    async def get_issue_async(self, issue_id: str) -> IssueFields | None:
        return await self._queries.get_issue(issue_id)

    @_sync_doc(LinearQueries.get_issue)
    def get_issue(self, issue_id: str) -> IssueFields | None:
        return self._run(self.get_issue_async(issue_id))

    async def list_issues_async(
        self,
        team_id: str,
        first: int = 50,
    ) -> list[IssueFields]:
        return await self._queries.list_issues(team_id, first)

    @_sync_doc(LinearQueries.list_issues)
    def list_issues(self, team_id: str, first: int = 50) -> list[IssueFields]:
        return self._run(self.list_issues_async(team_id, first))

    async def list_workflow_states_page_async(
        self,
        team_id: str,
        first: int = 50,
        after: str | None = None,
        *,
        include_archived: bool = False,
        order_by: PaginationOrderBy | None = None,
    ) -> ListWorkflowStatesResultWorkflowStates:
        return await self._queries.list_workflow_states_page(
            team_id,
            first=first,
            after=after,
            include_archived=include_archived,
            order_by=order_by,
        )

    @_sync_doc(LinearQueries.list_workflow_states_page)
    def list_workflow_states_page(
        self,
        team_id: str,
        first: int = 50,
        after: str | None = None,
        *,
        include_archived: bool = False,
        order_by: PaginationOrderBy | None = None,
    ) -> ListWorkflowStatesResultWorkflowStates:
        return self._run(
            self.list_workflow_states_page_async(
                team_id,
                first=first,
                after=after,
                include_archived=include_archived,
                order_by=order_by,
            ),
        )

    async def list_workflow_states_async(
        self,
        team_id: str,
        first: int = 50,
    ) -> list[WorkflowStateFields]:
        return await self._queries.list_workflow_states(team_id, first=first)

    @_sync_doc(LinearQueries.list_workflow_states)
    def list_workflow_states(
        self,
        team_id: str,
        first: int = 50,
    ) -> list[WorkflowStateFields]:
        return self._run(self.list_workflow_states_async(team_id, first=first))

    async def list_issues_page_async(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        first: int = 50,
        after: str | None = None,
        order_by: PaginationOrderBy | None = None,
        *,
        include_archived: bool = False,
    ) -> ListIssuesResultIssues:
        return await self._queries.list_issues_page(
            filter,
            first,
            after,
            order_by,
            include_archived=include_archived,
        )

    @_sync_doc(LinearQueries.list_issues_page)
    def list_issues_page(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        first: int = 50,
        after: str | None = None,
        order_by: PaginationOrderBy | None = None,
        *,
        include_archived: bool = False,
    ) -> ListIssuesResultIssues:
        return self._run(
            self.list_issues_page_async(
                filter,
                first,
                after,
                order_by,
                include_archived=include_archived,
            ),
        )

    async def list_open_team_issues_async(
        self,
        team_id: str,
        *,
        first: int = 100,
    ) -> list[IssueFields]:
        """List a team's unfinished issues, newest updated first."""
        page = await self.list_issues_page_async(
            {
                "team": {"id": {"eq": team_id}},
                "state": {"type": {"nin": ["completed", "canceled"]}},
            },
            first=first,
            order_by=PaginationOrderBy.updatedAt,
        )
        return list(page.nodes)

    def list_open_team_issues(
        self,
        team_id: str,
        *,
        first: int = 100,
    ) -> list[IssueFields]:
        """List a team's unfinished issues, newest updated first."""
        return self._run(self.list_open_team_issues_async(team_id, first=first))

    async def get_team_async(self, team_id: str) -> TeamFields | None:
        return await self._queries.get_team(team_id)

    @_sync_doc(LinearQueries.get_team)
    def get_team(self, team_id: str) -> TeamFields | None:
        return self._run(self.get_team_async(team_id))

    async def get_team_by_key_async(self, key: str) -> TeamFields | None:
        return await self._queries.get_team_by_key(key)

    @_sync_doc(LinearQueries.get_team_by_key)
    def get_team_by_key(self, key: str) -> TeamFields | None:
        return self._run(self.get_team_by_key_async(key))

    async def search_issues_async(
        self,
        term: str,
        first: int = 50,
        after: str | None = None,
    ) -> SearchIssuesResultSearchIssues:
        return await self._queries.search_issues(term, first, after)

    @_sync_doc(LinearQueries.search_issues)
    def search_issues(
        self,
        term: str,
        first: int = 50,
        after: str | None = None,
    ) -> SearchIssuesResultSearchIssues:
        return self._run(self.search_issues_async(term, first, after))

    async def get_user_async(self, user_id: str) -> UserFields | None:
        return await self._queries.get_user(user_id)

    @_sync_doc(LinearQueries.get_user)
    def get_user(self, user_id: str) -> UserFields | None:
        return self._run(self.get_user_async(user_id))

    async def get_viewer_async(self) -> UserFields:
        return await self._queries.get_viewer()

    @_sync_doc(LinearQueries.get_viewer)
    def get_viewer(self) -> UserFields:
        return self._run(self.get_viewer_async())

    def iter_issues_async(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        *,
        page_size: int = 50,
        limit: int | None = None,
        order_by: PaginationOrderBy | None = None,
        include_archived: bool = False,
    ) -> AsyncIterator[IssueFields]:
        return self._queries.iter_issues(
            filter,
            page_size=page_size,
            limit=limit,
            order_by=order_by,
            include_archived=include_archived,
        )

    def iter_issues(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        *,
        page_size: int = 50,
        limit: int | None = None,
        order_by: PaginationOrderBy | None = None,
        include_archived: bool = False,
    ) -> Iterator[IssueFields]:
        """Sync wrapper that materializes every page before yielding.

        Unlike :meth:`iter_issues_async`, ``next(...)`` blocks until
        :func:`asyncio.run` has followed every cursor through ``paginate`` and
        held the full result set in memory. Prefer the async iterator for large
        teams.
        """
        async_iterator = self.iter_issues_async(
            filter,
            page_size=page_size,
            limit=limit,
            order_by=order_by,
            include_archived=include_archived,
        )
        return iter(self._run(self._collect(async_iterator)))

    def iter_team_issues_async(
        self,
        team_id: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> AsyncIterator[IssueFields]:
        return self._queries.iter_team_issues(team_id, page_size=page_size, limit=limit)

    def iter_team_issues(
        self,
        team_id: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> Iterator[IssueFields]:
        """Sync team-issue iterator. See :meth:`iter_issues` for the materialization caveat."""
        return self.iter_issues(
            {"team": {"id": {"eq": team_id}}},
            page_size=page_size,
            limit=limit,
        )

    def iter_search_issues_async(
        self,
        term: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> AsyncIterator[IssueSearchResultFields]:
        return self._queries.iter_search_issues(term, page_size=page_size, limit=limit)

    def iter_search_issues(
        self,
        term: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> Iterator[IssueSearchResultFields]:
        """Sync search wrapper that materializes every page before yielding.

        Unlike :meth:`iter_search_issues_async`, ``next(...)`` blocks until
        :func:`asyncio.run` has followed every cursor through ``paginate`` and
        held the full result set in memory. Prefer the async iterator for large
        result sets.
        """
        async_iterator = self.iter_search_issues_async(
            term,
            page_size=page_size,
            limit=limit,
        )
        return iter(self._run(self._collect(async_iterator)))

    async def create_issue_async(self, input_: IssueCreateInput) -> IssueFields:
        return await self._mutations.create_issue(input_)

    @_sync_doc(LinearMutations.create_issue)
    def create_issue(self, input_: IssueCreateInput) -> IssueFields:
        return self._run(self.create_issue_async(input_))

    async def update_issue_async(
        self,
        issue_id: str,
        update: IssueUpdateInput,
    ) -> IssueFields:
        return await self._mutations.update_issue(issue_id, update)

    @_sync_doc(LinearMutations.update_issue)
    def update_issue(self, issue_id: str, update: IssueUpdateInput) -> IssueFields:
        return self._run(self.update_issue_async(issue_id, update))

    async def delete_issue_async(self, issue_id: str) -> bool:
        return await self._mutations.delete_issue(issue_id)

    @_sync_doc(LinearMutations.delete_issue)
    def delete_issue(self, issue_id: str) -> bool:
        return self._run(self.delete_issue_async(issue_id))

    async def create_comment_async(self, issue_id: str, body: str) -> CommentFields:
        return await self._mutations.create_comment(issue_id, body)

    @_sync_doc(LinearMutations.create_comment)
    def create_comment(self, issue_id: str, body: str) -> CommentFields:
        return self._run(self.create_comment_async(issue_id, body))

    def _run(self, awaitable: Awaitable[T]) -> T:
        """Run one operation and release its async session before closing the loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Every _run caller passes a coroutine, and asyncio.run would have
            # awaited it — but here it never gets started, so on collection Python
            # emits a "coroutine ... was never awaited" RuntimeWarning on top of the
            # RuntimeError below. Closing the coroutine first suppresses it, leaving
            # one actionable error. getattr keeps this working for any non-coroutine
            # Awaitable (e.g. a Future); see
            # test_sync_method_in_running_loop_emits_no_coroutine_warning.
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            msg = "asyncio.run() cannot be called from a running event loop"
            raise RuntimeError(msg)

        async def run_and_close() -> T:
            try:
                return await awaitable
            finally:
                await self._client.aclose()

        return asyncio.run(run_and_close())

    async def _collect(self, iterator: AsyncIterator[T]) -> list[T]:
        """Materialize an async iterator inside one event loop for sync callers."""
        return [item async for item in iterator]

    def close(self) -> None:
        """Close the facade's synchronous client resources."""
        self._client.close()

    async def aclose(self) -> None:
        """Close the facade's asynchronous client resources."""
        await self._client.aclose()
        self._client.close()

    def __enter__(self) -> Self:
        """Enter the synchronous context and return the facade itself."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the facade's synchronous resources on exit."""
        self.close()

    async def __aenter__(self) -> Self:
        """Enter the asynchronous context and return the facade itself."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Close both async and sync resources on exit."""
        await self.aclose()


__all__ = ["LinearWorkflow"]
