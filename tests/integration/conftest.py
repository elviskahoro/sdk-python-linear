"""Shared fixtures for the live-API integration suite.

The only input is ``LINEAR_API_KEY`` (skips, never fails, when absent). The
``team`` fixture uses an existing team, seeds it with issues and deletes them at
session end; it never creates or deletes teams. Use a key for a disposable workspace.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, TypeVar

import pytest
import pytest_asyncio

from gtm_linear import (
    IssueCreateInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
    LinearWorkflow,
)
from gtm_linear._generated.fragments import TeamFields
from gtm_linear.cli import _DotenvApiKey
from gtm_linear.exceptions import LinearGraphQLError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

T = TypeVar("T")

TITLE_PREFIX = "[sdk-it"


@pytest.fixture(scope="session")
def api_key() -> str:
    key = _DotenvApiKey().api_key.get_secret_value()
    if not key:
        pytest.skip("no LINEAR_API_KEY available for live integration tests")
    return key


@pytest.fixture(scope="session")
def run_id() -> str:
    return os.environ.get("LINEAR_IT_RUN_ID") or uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def title_prefix(run_id: str) -> str:
    return f"{TITLE_PREFIX} {run_id}]"


def _is_rate_limited(exc: LinearGraphQLError) -> bool:
    return "RATELIMITED" in exc.codes or "RATE_LIMIT_ERROR" in exc.codes


async def with_backoff(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
) -> T:
    """Retry ``call`` on Linear rate limiting. Lives in the harness, not the SDK."""
    delay = 2.0
    for attempt in range(attempts):
        try:
            return await call()
        except LinearGraphQLError as exc:
            if not _is_rate_limited(exc) or attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    msg = "unreachable"
    raise AssertionError(msg)


def _with_backoff_sync(
    call: Callable[[], T],
    *,
    attempts: int = 5,
) -> T:
    """Sync twin of ``with_backoff`` for the session-scoped ``team`` fixture."""
    delay = 2.0
    for attempt in range(attempts):
        try:
            return call()
        except LinearGraphQLError as exc:
            if not _is_rate_limited(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    msg = "unreachable"
    raise AssertionError(msg)


@pytest_asyncio.fixture
async def client(api_key: str) -> AsyncIterator[LinearClient]:
    async with LinearClient(api_key=api_key) as c:
        yield c


@pytest.fixture
def queries(client: LinearClient) -> LinearQueries:
    return LinearQueries(client)


@pytest.fixture
def mutations(client: LinearClient) -> LinearMutations:
    return LinearMutations(client)


@pytest.fixture
def workflow(api_key: str) -> Iterator[LinearWorkflow]:
    with LinearWorkflow(api_key) as wf:
        yield wf


# An issue younger than this may still belong to a sibling integration-tests run
# (overlapping main pushes, the weekly cron, manual dispatches), so the sweep
# leaves it alone; crashed-run leftovers outlive the guard and go.
SWEEP_MIN_ISSUE_AGE = timedelta(minutes=45)
_LIST_TEAMS = "query { teams(first: 50) { nodes { id name key } } }"
_LIST_TEST_ISSUES = (
    "query($team: ID!, $prefix: String!, $after: String) {"
    " issues(first: 100, after: $after, includeArchived: false, filter:"
    " { team: { id: { eq: $team } }, title: { startsWith: $prefix } })"
    " { nodes { id createdAt title } pageInfo { hasNextPage endCursor } } }"
)
_DELETE_ISSUE = "mutation($id: String!) { issueDelete(id: $id) { success } }"


def _delete_test_issues(
    client: LinearClient,
    team_id: str,
    *,
    run_prefix: str | None,
) -> None:
    """Delete ``[sdk-it`` issues in the team.

    With ``run_prefix`` set, delete only that run's issues (teardown). Otherwise
    sweep issues leaked by crashed earlier runs, sparing young ones a sibling run
    may still be using.
    """
    after: str | None = None
    while True:
        page = _with_backoff_sync(
            partial(
                client.execute,
                _LIST_TEST_ISSUES,
                {"team": team_id, "prefix": run_prefix or TITLE_PREFIX, "after": after},
            ),
        )["issues"]
        for node in page["nodes"]:
            if run_prefix is None:
                created = datetime.fromisoformat(node["createdAt"])
                if datetime.now(UTC) - created < SWEEP_MIN_ISSUE_AGE:
                    continue
            _with_backoff_sync(
                partial(client.execute, _DELETE_ISSUE, {"id": node["id"]}),
            )
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]


@pytest.fixture(scope="session")
def team(api_key: str, title_prefix: str) -> Iterator[TeamFields]:
    """An existing team to test against, seeded with issues, cleaned up after.

    The suite never creates or deletes teams (free Linear plans cap them, and
    deleted teams keep counting). It uses the team whose key is
    ``LINEAR_IT_TEAM_KEY`` or, if unset, the first team in the workspace.
    Every issue it creates is titled ``[sdk-it <run id>] ...`` and is deleted at
    session end; leaked ``[sdk-it`` issues from crashed runs are swept first.
    Point the key at a disposable workspace.
    """
    with LinearClient(api_key=api_key) as client:
        teams = _with_backoff_sync(partial(client.execute, _LIST_TEAMS))["teams"][
            "nodes"
        ]
        wanted = os.environ.get("LINEAR_IT_TEAM_KEY")
        picked = [t for t in teams if wanted in (None, t["key"])]
        if not picked:
            pytest.fail(
                "no team available for live integration tests"
                + (f" (LINEAR_IT_TEAM_KEY={wanted})" if wanted else ""),
            )
        team = TeamFields(**picked[0])
        _delete_test_issues(client, team.id, run_prefix=None)
        try:
            with LinearWorkflow(api_key) as wf:
                for n in range(3):
                    seed = IssueCreateInput(
                        title=f"{title_prefix} seed {n}",
                        team_id=team.id,
                    )
                    _with_backoff_sync(partial(wf.create_issue, seed))
            yield team
        finally:
            _delete_test_issues(client, team.id, run_prefix=title_prefix)
