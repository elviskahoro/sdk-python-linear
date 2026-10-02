"""Cursor-following and stall-detection tests for paginate()."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import NoReturn

import httpx
import pytest
import respx

from gtm_linear import LinearClient, LinearQueries
from gtm_linear.exceptions import LinearPaginationError
from gtm_linear.pagination import MAX_CONSECUTIVE_EMPTY_PAGES, paginate
from tests.conftest import API_URL, issue_payload, page_info_payload


def _page(ids: list[str], *, has_next: bool, end: str | None) -> dict[str, object]:
    return {
        "data": {
            "issues": {
                "nodes": [issue_payload(i) for i in ids],
                "pageInfo": page_info_payload(has_next=has_next, end=end),
            },
        },
    }


@dataclass
class _FakePageInfo:
    """The ``page_info`` half of paginate's structural protocol."""

    has_next_page: bool
    end_cursor: str | None


@dataclass
class _FakeConn:
    """A minimal typed connection satisfying paginate's protocol, without HTTP.

    Typed (rather than SimpleNamespace) so the ``fetch`` handlers below match
    ``Callable[[str | None], Awaitable[_Connection[T]]]`` statically.
    """

    nodes: list[str]
    page_info: _FakePageInfo


def _conn(ids: list[str], *, has_next: bool, end: str | None) -> _FakeConn:
    """Build one fake connection page."""
    return _FakeConn(
        nodes=list(ids),
        page_info=_FakePageInfo(has_next_page=has_next, end_cursor=end),
    )


async def test_iter_team_issues_follows_cursors() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json=_page(["a", "b"], has_next=True, end="cur-1")),
            httpx.Response(200, json=_page(["c"], has_next=False, end=None)),
        ]
        async with LinearClient(api_key="key") as client:
            issues = [i async for i in LinearQueries(client).iter_team_issues("team-1")]

    assert [i.id for i in issues] == ["a", "b", "c"]
    # The second request must carry the first page's end cursor.
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert first["variables"]["after"] is None
    assert second["variables"]["after"] == "cur-1"


async def test_iter_issues_respects_limit_and_stops_early() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json=_page(["a", "b"], has_next=True, end="cur-1")),
            httpx.Response(200, json=_page(["c", "d"], has_next=True, end="cur-2")),
        ]
        async with LinearClient(api_key="key") as client:
            issues = [
                i
                async for i in LinearQueries(client).iter_team_issues(
                    "team-1",
                    limit=3,
                )
            ]

    assert [i.id for i in issues] == ["a", "b", "c"]
    # Stops as soon as the limit is hit; it must not fetch a third page.
    assert len(route.calls) == 2


async def test_pagination_stops_when_next_page_has_no_cursor() -> None:
    """A connection claiming another page but returning no cursor must not loop."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json=_page(["a"], has_next=True, end=None),
            ),
        )
        async with LinearClient(api_key="key") as client:
            issues = [i async for i in LinearQueries(client).iter_team_issues("t")]

    assert [i.id for i in issues] == ["a"]


async def test_pagination_with_zero_limit_does_not_fetch() -> None:
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> NoReturn:
        calls.append(cursor)
        raise AssertionError("fetch must not be called for a zero limit")

    issues = [issue async for issue in paginate(fetch, limit=0)]

    assert issues == []
    assert calls == []


async def test_pagination_stops_when_cursor_does_not_advance() -> None:
    """A connection that keeps returning a truthy but non-advancing cursor must not
    refetch the same page forever. Regression coverage for the sticky-cursor case.
    """
    with respx.mock:
        route = respx.post(API_URL)

        async def handler(request):
            body = json.loads(request.content)
            after = body["variables"]["after"]
            if after is None:
                return httpx.Response(
                    200,
                    json=_page(["a", "b"], has_next=True, end="cur-1"),
                )
            return httpx.Response(200, json=_page(["c"], has_next=True, end="cur-1"))

        route.mock(side_effect=handler)
        async with LinearClient(api_key="key") as client:
            issues = [
                i
                async for i in LinearQueries(client).iter_issues(
                    {"team": {"id": {"eq": "t"}}},
                    page_size=50,
                )
            ]

    assert [i.id for i in issues] == ["a", "b", "c"]
    # The server returned the same cursor it was just fed; pagination must stop
    # instead of re-issuing byte-identical requests forever.
    assert len(route.calls) == 2
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert first["variables"]["after"] is None
    assert second["variables"]["after"] == "cur-1"


async def test_pagination_follows_advancing_cursors_across_multiple_pages() -> None:
    """A genuinely advancing cursor must not be cut short by the non-advance guard."""
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json=_page(["a"], has_next=True, end="cur-1")),
            httpx.Response(200, json=_page(["b"], has_next=True, end="cur-2")),
            httpx.Response(200, json=_page(["c"], has_next=False, end=None)),
        ]
        async with LinearClient(api_key="key") as client:
            issues = [i async for i in LinearQueries(client).iter_team_issues("t")]

    assert [i.id for i in issues] == ["a", "b", "c"]
    assert len(route.calls) == 3
    afters = [json.loads(c.request.content)["variables"]["after"] for c in route.calls]
    assert afters == [None, "cur-1", "cur-2"]


async def test_pagination_tolerates_a_transient_empty_page() -> None:
    """An empty page followed by real nodes must not end iteration early.

    Linear can compute ``hasNextPage`` from pre-filter metadata while the
    ``nodes`` slice comes back empty; the page after the advanced cursor
    usually recovers, which is exactly why empty pages are tolerated instead
    of stopping at the first one.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if cursor is None:
            return _conn(["a", "b"], has_next=True, end="cur-1")
        if cursor == "cur-1":
            return _conn([], has_next=True, end="cur-2")
        return _conn(["c"], has_next=False, end=None)

    issues = [issue async for issue in paginate(fetch)]

    assert issues == ["a", "b", "c"]
    assert calls == [None, "cur-1", "cur-2"]


async def test_pagination_raises_when_empty_pages_never_recover() -> None:
    """Direct generator regression: an always-empty page with a perpetually
    advancing cursor and ``hasNextPage=True`` must not loop forever. Before
    the guard existed this constructed an infinite request loop; now the
    tolerance budget runs out and the stall is reported loudly.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if len(calls) > MAX_CONSECUTIVE_EMPTY_PAGES + 2:
            raise AssertionError("would loop forever without the empty-page guard")
        return _conn([], has_next=True, end=f"cur-{len(calls)}")

    collected: list[object] = []
    with pytest.raises(LinearPaginationError, match="no nodes"):
        async for issue in paginate(fetch):
            collected.append(issue)

    assert collected == []
    assert len(calls) == MAX_CONSECUTIVE_EMPTY_PAGES


async def test_iter_team_issues_raises_on_stalled_connection() -> None:
    """The HTTP-level view: ``iter_*`` consumers inherit the stall signal and
    keep the nodes yielded before the connection stalled.
    """
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json=_page(["a", "b"], has_next=True, end="cur-1")),
            *(
                httpx.Response(200, json=_page([], has_next=True, end=f"cur-{i}"))
                for i in range(2, 2 + MAX_CONSECUTIVE_EMPTY_PAGES)
            ),
            # Safety valve: one response beyond what pagination should consume.
            httpx.Response(200, json=_page([], has_next=True, end="cur-valve")),
        ]
        collected: list[str] = []
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearPaginationError, match="no nodes"):
                async for issue in LinearQueries(client).iter_team_issues("t"):
                    collected.append(issue.id)

    assert collected == ["a", "b"]
    # One page of nodes, then the full budget of empty pages — nothing more.
    assert len(route.calls) == 1 + MAX_CONSECUTIVE_EMPTY_PAGES
    afters = [json.loads(c.request.content)["variables"]["after"] for c in route.calls]
    assert afters == [None, "cur-1", "cur-2", "cur-3"]


async def test_pagination_ends_cleanly_when_an_empty_page_closes_the_connection() -> (
    None
):
    """An empty page that stops claiming another page is a normal end, not a
    stall: no error, no refetch.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        return _conn([], has_next=False, end=None)

    issues = [issue async for issue in paginate(fetch)]

    assert issues == []
    assert calls == [None]


async def test_pagination_limit_survives_trailing_empty_page() -> None:
    """A trailing empty page after real nodes ends iteration cleanly even with
    a limit in play: the limit guard only fires inside the node loop, and the
    empty page must close the connection without raising.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if cursor is None:
            return _conn(["a", "b"], has_next=True, end="cur-1")
        return _conn([], has_next=False, end=None)

    issues = [issue async for issue in paginate(fetch, limit=10)]

    assert issues == ["a", "b"]
    assert calls == [None, "cur-1"]


async def test_pagination_stops_silently_when_an_empty_page_lacks_a_cursor() -> None:
    """A malformed empty page — claims another page, returns no cursor — keeps
    the missing-cursor guard's silent stop; it must not raise.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if cursor is None:
            return _conn(["a", "b"], has_next=True, end="cur-1")
        return _conn([], has_next=True, end=None)

    issues = [issue async for issue in paginate(fetch)]

    assert issues == ["a", "b"]
    assert calls == [None, "cur-1"]


async def test_pagination_stops_silently_when_an_empty_page_repeats_the_cursor() -> (
    None
):
    """A malformed empty page — claims another page, sticky cursor — keeps the
    sticky-cursor guard's silent stop; it must not raise.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if cursor is None:
            return _conn(["a", "b"], has_next=True, end="cur-1")
        return _conn([], has_next=True, end="cur-1")

    issues = [issue async for issue in paginate(fetch)]

    assert issues == ["a", "b"]
    assert calls == [None, "cur-1"]


@pytest.mark.parametrize(
    "final_end",
    [None, "cur-3"],
    ids=["missing-cursor", "sticky-cursor"],
)
async def test_pagination_cursor_guards_win_over_the_empty_page_budget(
    final_end: str | None,
) -> None:
    """Even at the exhaustion boundary a malformed empty page stops silently:
    the missing-cursor and sticky-cursor guards run before the empty-page
    budget, so the same page shape behaves the same no matter how many empty
    pages preceded it.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        if cursor is None:
            return _conn(["a", "b"], has_next=True, end="cur-1")
        if cursor == "cur-1":
            return _conn([], has_next=True, end="cur-2")
        if cursor == "cur-2":
            return _conn([], has_next=True, end="cur-3")
        return _conn([], has_next=True, end=final_end)

    issues = [issue async for issue in paginate(fetch)]

    assert issues == ["a", "b"]
    # Two tolerated empty pages, then a third whose cursor is unusable:
    # a silent stop, not a LinearPaginationError.
    assert calls == [None, "cur-1", "cur-2", "cur-3"]


async def test_pagination_limit_does_not_mask_a_stall() -> None:
    """A limit that empty pages never reach must not suppress the stall
    signal: the limit guard only fires inside the node loop, so a connection
    that only ever returns empty pages raises once the budget is spent.
    """
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> _FakeConn:
        calls.append(cursor)
        return _conn([], has_next=True, end=f"cur-{len(calls)}")

    collected: list[object] = []
    with pytest.raises(LinearPaginationError, match="no nodes"):
        async for issue in paginate(fetch, limit=5):
            collected.append(issue)

    assert collected == []
    assert len(calls) == MAX_CONSECUTIVE_EMPTY_PAGES
