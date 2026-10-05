"""Cursor pagination over Linear connections.

Linear's connections are Relay-style: every page carries a ``pageInfo`` with an
``endCursor`` and a ``hasNextPage`` flag. The SDK modelled ``PageInfo`` for a while
without ever using it — this is what makes it useful.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from .exceptions import LinearPaginationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

T = TypeVar("T")


class _PageInfo(Protocol):
    """The page metadata ``paginate`` reads off a connection.

    Declared with read-only properties: protocol *attributes* are mutable and
    therefore invariant, which would reject every concrete ``PageInfo`` model
    (they are distinct classes, not subclasses of this protocol). Read-only
    properties match covariantly, so any type with the two attributes below
    satisfies the protocol.
    """

    @property
    def has_next_page(self) -> bool: ...

    @property
    def end_cursor(self) -> str | None: ...


class _Connection(Protocol[T]):
    """A Relay connection page: ``nodes`` plus the ``page_info`` protocol above."""

    @property
    def nodes(self) -> list[T]: ...

    @property
    def page_info(self) -> _PageInfo: ...


# How many consecutive nodeless pages to refetch before declaring the
# connection stalled. Linear can compute ``hasNextPage`` from pre-filter
# metadata while the ``nodes`` slice comes back empty (``searchIssues``
# server-side dedup, concurrent deletes emptying a page), and the page after
# the advanced cursor usually recovers, so a small budget of empty pages is
# worth refetching. Three bounds the wasted round trips while tolerating every
# transient case observed so far.
MAX_CONSECUTIVE_EMPTY_PAGES = 3


async def paginate(
    fetch: Callable[[str | None], Awaitable[_Connection[T]]],
    *,
    limit: int | None = None,
    strict_cursor: bool = False,
) -> AsyncIterator[T]:
    """Yield every node across pages, following cursors until exhausted.

    Iteration ends without error when the connection stops claiming another
    page or when ``limit`` is reached. By default, a missing or non-advancing
    cursor ends iteration without error for compatibility. Set ``strict_cursor``
    to raise ``LinearPaginationError`` for malformed continuation pages.
    Empty pages are different: Linear can compute ``hasNextPage``
    from pre-filter metadata while the ``nodes`` slice comes back empty
    (``searchIssues`` server-side dedup, concurrent deletes emptying a page),
    so a small run of them is tolerated and refetched — the page after the
    advanced cursor usually recovers. Once ``MAX_CONSECUTIVE_EMPTY_PAGES``
    consecutive pages have returned no nodes, the connection is treated as
    stalled and iteration raises rather than silently truncating results.

    Args:
        fetch: Called with a cursor (None for the first page) and returning a
            connection with ``nodes`` and ``page_info``.
        limit: Stop after yielding this many nodes. None means no limit.
        strict_cursor: Raise when a page claims another page but its cursor is
            missing or does not advance. Defaults to False for compatibility.

    Yields:
        Each node, in page order.

    Raises:
        LinearPaginationError: In strict mode, when a continuation cursor is
            missing or does not advance; or when
            ``MAX_CONSECUTIVE_EMPTY_PAGES`` consecutive pages return no nodes
            while claiming another page with a fresh cursor. Nodes already
            yielded remain with the caller.

    Example:
        >>> async for issue in paginate(
        ...     lambda cursor: queries.list_issues_page(flt, after=cursor),
        ...     limit=200,
        ... ):
        ...     print(issue.identifier)
    """
    if limit is not None and limit <= 0:
        return

    cursor: str | None = None
    yielded = 0
    empty_pages = 0

    while True:
        page = await fetch(cursor)
        for node in page.nodes:
            yield node
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        # Track runs of empty pages; the budget check after the cursor guards
        # decides when such a run is a stall. An empty page yields nothing on
        # its own, but the next fetch carries a fresh cursor and may recover
        # (see MAX_CONSECUTIVE_EMPTY_PAGES) — which is why empty pages are
        # refetched at all rather than ending iteration at the first one.
        if not page.nodes:
            empty_pages += 1
        else:
            empty_pages = 0

        if not page.page_info.has_next_page:
            return
        # Guard on the cursor as well as the flag: a connection that claims another
        # page but returns no cursor would otherwise refetch page one forever.
        if not page.page_info.end_cursor:
            if strict_cursor:
                msg = "pageInfo.hasNextPage is true but endCursor is missing"
                raise LinearPaginationError(msg)
            return
        # A connection that returns the same cursor it was just fed is not making
        # forward progress; continuing would refetch the same page forever.
        if page.page_info.end_cursor == cursor:
            if strict_cursor:
                msg = "pageInfo.endCursor did not advance to a new cursor"
                raise LinearPaginationError(msg)
            return
        # The empty-page budget sits after the cursor guards so a malformed page
        # is handled the same way no matter how many empty pages came before it.
        # In compatibility mode, missing and sticky cursors stop silently; strict
        # mode raises for both. Only a page that still claims another page with a
        # fresh, advancing cursor — after MAX_CONSECUTIVE_EMPTY_PAGES consecutive
        # nodeless rounds — is a stall, and raising beats refetching forever and
        # silently truncating results. (The ``limit`` guard above cannot catch this
        # case: it lives inside the node loop, which an empty page never enters.)
        if empty_pages >= MAX_CONSECUTIVE_EMPTY_PAGES:
            msg = (
                f"{empty_pages} consecutive pages returned no nodes while "
                "pageInfo.hasNextPage stayed true"
            )
            raise LinearPaginationError(msg)
        cursor = page.page_info.end_cursor
