"""Typed read operations against the Linear GraphQL API.

Every document and every model here is generated from Linear's schema by
scripts/codegen.py — see operations/*.graphql for the selection sets. Nothing in
this module parses a response by hand; `model_validate` does the work, which is why
the hand-rolled `_parse_issue`/`_parse_user` helpers are gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._generated.GetIssue import DOCUMENT as GET_ISSUE
from ._generated.GetIssue import GetIssueResult
from ._generated.GetTeam import DOCUMENT as GET_TEAM
from ._generated.GetTeam import GetTeamResult
from ._generated.GetTeamByKey import DOCUMENT as GET_TEAM_BY_KEY
from ._generated.GetTeamByKey import GetTeamByKeyResult
from ._generated.GetUser import DOCUMENT as GET_USER
from ._generated.GetUser import GetUserResult
from ._generated.GetViewer import DOCUMENT as GET_VIEWER
from ._generated.GetViewer import GetViewerResult
from ._generated.ListIssues import DOCUMENT as LIST_ISSUES
from ._generated.ListIssues import (
    ListIssuesResult,
    ListIssuesResultIssues,
    PaginationOrderBy,
)
from ._generated.SearchIssues import DOCUMENT as SEARCH_ISSUES
from ._generated.SearchIssues import SearchIssuesResult, SearchIssuesResultSearchIssues
from ._generated.fragments import IssueSearchResultFields
from .pagination import paginate

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ._generated.fragments import (
        IssueFields,
        TeamFields,
        UserFields,
    )
    from .client import LinearClient


class LinearQueries:
    """Typed query methods for common Linear read operations."""

    def __init__(self, client: LinearClient) -> None:
        """Initialize LinearQueries.

        Args:
            client: LinearClient instance for API access.
        """
        self._client = client

    async def get_issue(self, issue_id: str) -> IssueFields | None:
        """Fetch a single issue by ID.

        Args:
            issue_id: The Linear issue ID or identifier (for example ``ENG-123``).

        Returns:
            The issue, or None if it does not exist.
        """
        data = await self._client.execute_async(GET_ISSUE, {"id": issue_id})
        if not data.get("issue"):
            return None
        return GetIssueResult.model_validate(data).issue

    async def list_issues(self, team_id: str, first: int = 50) -> list[IssueFields]:
        """List issues for a team.

        Args:
            team_id: The Linear team ID.
            first: Maximum number of issues to return (default 50).

        Returns:
            A list of issues. Use :meth:`list_issues_page` when you need the cursor.
        """
        page = await self.list_issues_page(
            {"team": {"id": {"eq": team_id}}},
            first=first,
        )
        return page.nodes

    async def list_issues_page(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        first: int = 50,
        after: str | None = None,
        order_by: PaginationOrderBy | None = None,
        *,
        include_archived: bool = False,
    ) -> ListIssuesResultIssues:
        """List a filtered page of issues, with cursor metadata.

        Args:
            filter: A Linear ``IssueFilter`` as a plain dict, e.g.
                ``{"team": {"id": {"eq": team_id}}}``. Passed through untouched and
                validated server-side; see Linear's schema for the full grammar.
            first: Page size.
            after: Cursor from a previous page's ``page_info.end_cursor``.
            order_by: Sort field.
            include_archived: Whether to include archived issues.

        Returns:
            The page: ``.nodes`` and ``.page_info``.
        """
        data = await self._client.execute_async(
            LIST_ISSUES,
            {
                "filter": filter,
                "first": first,
                "after": after,
                "orderBy": order_by.value if order_by else None,
                "includeArchived": include_archived,
            },
        )
        return ListIssuesResult.model_validate(data).issues

    async def get_team(self, team_id: str) -> TeamFields | None:
        """Fetch a single team by ID.

        Args:
            team_id: The Linear team ID.

        Returns:
            The team, or None if it does not exist.
        """
        data = await self._client.execute_async(GET_TEAM, {"id": team_id})
        if not data.get("team"):
            return None
        return GetTeamResult.model_validate(data).team

    async def get_team_by_key(self, key: str) -> TeamFields | None:
        """Fetch a single team by its human-readable key, such as ``ENG``.

        Args:
            key: The team key.

        Returns:
            The team, or None if no team has that key.
        """
        data = await self._client.execute_async(GET_TEAM_BY_KEY, {"key": key})
        nodes = GetTeamByKeyResult.model_validate(data).teams.nodes
        return nodes[0] if nodes else None

    async def search_issues(
        self,
        term: str,
        first: int = 50,
        after: str | None = None,
    ) -> SearchIssuesResultSearchIssues:
        """Full-text search for issues.

        Args:
            term: The search term.
            first: Page size.
            after: Cursor from a previous page's ``page_info.end_cursor``.

        Returns:
            The page: ``.nodes`` and ``.page_info``.

        Note:
            Linear returns ``IssueSearchResult``, a distinct type from ``Issue``,
            so nodes are :class:`IssueSearchResultFields`.
        """
        data = await self._client.execute_async(
            SEARCH_ISSUES,
            {"term": term, "first": first, "after": after},
        )
        return SearchIssuesResult.model_validate(data).search_issues

    async def get_user(self, user_id: str) -> UserFields | None:
        """Fetch a single user by ID.

        Args:
            user_id: The Linear user ID.

        Returns:
            The user, or None if they do not exist.
        """
        data = await self._client.execute_async(GET_USER, {"id": user_id})
        if not data.get("user"):
            return None
        return GetUserResult.model_validate(data).user

    async def get_viewer(self) -> UserFields:
        """Fetch the user the API key authenticates as.

        Returns:
            The authenticated user. Useful as a credential check.
        """
        data = await self._client.execute_async(GET_VIEWER)
        return GetViewerResult.model_validate(data).viewer

    def iter_issues(
        self,
        filter: dict[str, Any] | None = None,  # noqa: A002
        *,
        page_size: int = 50,
        limit: int | None = None,
        order_by: PaginationOrderBy | None = None,
        include_archived: bool = False,
    ) -> AsyncIterator[IssueFields]:
        """Iterate every issue matching a filter, following cursors automatically.

        Args:
            filter: A Linear ``IssueFilter`` as a plain dict.
            page_size: How many issues to request per round trip.
            limit: Stop after this many issues. None fetches everything.
            order_by: Sort field.
            include_archived: Whether to include archived issues.

        Returns:
            An async iterator over issues.

        Example:
            >>> async for issue in queries.iter_issues({"team": {"id": {"eq": tid}}}):
            ...     print(issue.identifier)
        """

        async def fetch(cursor: str | None) -> ListIssuesResultIssues:
            return await self.list_issues_page(
                filter,
                first=page_size,
                after=cursor,
                order_by=order_by,
                include_archived=include_archived,
            )

        return paginate(fetch, limit=limit)

    def iter_team_issues(
        self,
        team_id: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> AsyncIterator[IssueFields]:
        """Iterate every issue on a team.

        Args:
            team_id: The Linear team ID.
            page_size: How many issues to request per round trip.
            limit: Stop after this many issues.

        Returns:
            An async iterator over the team's issues.
        """
        return self.iter_issues(
            {"team": {"id": {"eq": team_id}}},
            page_size=page_size,
            limit=limit,
        )

    def iter_search_issues(
        self,
        term: str,
        *,
        page_size: int = 50,
        limit: int | None = None,
    ) -> AsyncIterator[IssueSearchResultFields]:
        """Iterate every search hit, following cursors automatically.

        Args:
            term: The search term.
            page_size: How many results to request per round trip.
            limit: Stop after this many results.

        Returns:
            An async iterator over search results.
        """

        async def fetch(cursor: str | None) -> SearchIssuesResultSearchIssues:
            return await self.search_issues(term, first=page_size, after=cursor)

        return paginate(fetch, limit=limit)


__all__ = ["LinearQueries", "PaginationOrderBy", "IssueSearchResultFields"]
