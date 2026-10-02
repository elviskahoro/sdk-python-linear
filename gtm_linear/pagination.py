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
    has_next_page: bool
    end_cursor: str | None


class _Connection(Protocol[T]):
    nodes: list[T]
    page_info: _PageInfo


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
) -> AsyncIterator[T]:
    """Yield every node across pages, following cursors until exhausted.

    Iteration ends without error when the connection stops claiming another
    page, when ``page_info`` offers no cursor to advance to, or when ``limit``
    is reached. Empty pages are different: Linear can compute ``hasNextPage``
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

    Yields:
        Each node, in page order.

    Raises:
        LinearPaginationError: ``MAX_CONSECUTIVE_EMPTY_PAGES`` consecutive
            pages returned no nodes while ``page_info`` still claimed another
            page with a fresh cursor. Nodes already yielded remain with the
            caller.

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

        # Guard on the cursor as well as the flag: a connection that claims another
        # page but returns no cursor would otherwise refetch page one forever.
        if not page.page_info.has_next_page or not page.page_info.end_cursor:
            return
        # A connection that returns the same cursor it was just fed is not making
        # forward progress; continuing would refetch the same page forever.
        if page.page_info.end_cursor == cursor:
            return
        # The empty-page budget sits after the cursor guards so a malformed page
        # ends the same way no matter how many empty pages came before it:
        # missing-cursor and sticky-cursor pages always stop silently. Only a
        # page that still claims another page with a fresh, advancing cursor —
        # after MAX_CONSECUTIVE_EMPTY_PAGES consecutive nodeless rounds — is a
        # stall, and raising beats both refetching forever and silently
        # truncating results. (The ``limit`` guard above cannot catch this
        # case: it lives inside the node loop, which an empty page never
        # enters.)
        if empty_pages >= MAX_CONSECUTIVE_EMPTY_PAGES:
            raise LinearPaginationError(
                f"{empty_pages} consecutive pages returned no nodes while "
                "pageInfo.hasNextPage stayed true"
            )
        cursor = page.page_info.end_cursor
