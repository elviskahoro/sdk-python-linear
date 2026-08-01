"""Cursor-following tests."""

from __future__ import annotations

import json

import httpx
import respx

from gtm_linear import LinearClient, LinearQueries
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


async def test_iter_team_issues_follows_cursors() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        route.side_effect = [
            httpx.Response(200, json=_page(["a", "b"], has_next=True, end="cur-1")),
            httpx.Response(200, json=_page(["c"], has_next=False, end=None)),
        ]
        async with LinearClient(api_key="key") as client:
            issues = [i async for i in LinearQueries(client).iter_team_issues("team-1")]

    assert [i.id for i in issues] == ["a", "b", "c"]  # noqa: S101
    # The second request must carry the first page's end cursor.
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert first["variables"]["after"] is None  # noqa: S101
    assert second["variables"]["after"] == "cur-1"  # noqa: S101


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

    assert [i.id for i in issues] == ["a", "b", "c"]  # noqa: S101
    # Stops as soon as the limit is hit; it must not fetch a third page.
    assert len(route.calls) == 2  # noqa: S101


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

    assert [i.id for i in issues] == ["a"]  # noqa: S101


async def test_pagination_with_zero_limit_does_not_fetch() -> None:
    calls: list[str | None] = []

    async def fetch(cursor: str | None) -> object:
        calls.append(cursor)
        raise AssertionError("fetch must not be called for a zero limit")

    from gtm_linear.pagination import paginate

    issues = [issue async for issue in paginate(fetch, limit=0)]

    assert issues == []  # noqa: S101
    assert calls == []  # noqa: S101
