"""Cursor pagination over Linear connections.

Linear's connections are Relay-style: every page carries a ``pageInfo`` with an
``endCursor`` and a ``hasNextPage`` flag. The SDK modelled ``PageInfo`` for a while
without ever using it — this is what makes it useful.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

T = TypeVar("T")


class _PageInfo(Protocol):
    has_next_page: bool
    end_cursor: str | None


class _Connection(Protocol[T]):
    nodes: list[T]
    page_info: _PageInfo


async def paginate(
    fetch: Callable[[str | None], Awaitable[_Connection[T]]],
    *,
    limit: int | None = None,
) -> AsyncIterator[T]:
    """Yield every node across pages, following cursors until exhausted.

    Args:
        fetch: Called with a cursor (None for the first page) and returning a
            connection with ``nodes`` and ``page_info``.
        limit: Stop after yielding this many nodes. None means no limit.

    Yields:
        Each node, in page order.

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

    while True:
        page = await fetch(cursor)
        for node in page.nodes:
            yield node
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        # Guard on the cursor as well as the flag: a connection that claims another
        # page but returns no cursor would otherwise refetch page one forever.
        if not page.page_info.has_next_page or not page.page_info.end_cursor:
            return
        cursor = page.page_info.end_cursor
