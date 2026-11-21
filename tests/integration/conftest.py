"""Shared fixtures for the live-API integration suite.

The only input is ``LINEAR_API_KEY`` (skips, never fails, when absent). The
``team`` fixture scaffolds everything else: a disposable team seeded with issues,
deleted at session end. Use a key for a disposable workspace.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import string
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


TEAM_NAME_PREFIX = "sdk-it"
# A team younger than this may still belong to a sibling integration-tests run
# (overlapping main pushes, the weekly cron, manual dispatches), so the
# sweep leaves it alone; crashed-run leftovers outlive the guard and go.
SWEEP_MIN_TEAM_AGE = timedelta(minutes=45)
_CREATE_TEAM = (
    "mutation($i: TeamCreateInput!) { teamCreate(input: $i) { team { id name key } } }"
)
_DELETE_TEAM = "mutation($id: String!) { teamDelete(id: $id) { success } }"
_LIST_TEAMS = (
    "query($after: String) { teams(first: 100, after: $after)"
    " { nodes { id name createdAt } pageInfo { hasNextPage endCursor } } }"
)


def _sweep_leaked_teams(client: LinearClient) -> None:
    """Delete ``sdk-it`` teams leaked by crashed earlier runs.

    Teams younger than ``SWEEP_MIN_TEAM_AGE`` are skipped: a concurrent
    integration-tests run may still be executing against one. The listing paginates
    so workspaces with more than 100 teams sweep past page 1.
    """
    after: str | None = None
    while True:
        page = _with_backoff_sync(
            partial(client.execute, _LIST_TEAMS, {"after": after}),
        )["teams"]
        for node in page["nodes"]:
            if not node["name"].startswith(TEAM_NAME_PREFIX):
                continue
            created = datetime.fromisoformat(node["createdAt"])
            if datetime.now(UTC) - created < SWEEP_MIN_TEAM_AGE:
                continue
            _with_backoff_sync(
                partial(client.execute, _DELETE_TEAM, {"id": node["id"]}),
            )
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]


@pytest.fixture(scope="session")
def team(api_key: str, run_id: str) -> Iterator[TeamFields]:
    """A disposable team created for this run, seeded with issues, deleted after.

    The suite needs only ``LINEAR_API_KEY``: it builds the team it tests
    against, so no pre-provisioned workspace state is assumed. Teams leaked by
    crashed earlier runs (name prefix ``sdk-it``) are swept first, sparing
    young ones a sibling run may still be using. Because the suite only ever
    deletes teams it created or recognises by that prefix, point it at a
    disposable workspace.
    """
    with LinearClient(api_key=api_key) as client:
        _sweep_leaked_teams(client)
        letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
        created = _with_backoff_sync(
            partial(
                client.execute,
                _CREATE_TEAM,
                {"i": {"name": f"{TEAM_NAME_PREFIX} {run_id}", "key": f"IT{letters}"}},
            ),
        )["teamCreate"]["team"]
        team = TeamFields(**created)
        try:
            with LinearWorkflow(api_key) as wf:
                for n in range(3):
                    seed = IssueCreateInput(title=f"seed {n}", team_id=team.id)
                    _with_backoff_sync(partial(wf.create_issue, seed))
            yield team
        finally:
            _with_backoff_sync(partial(client.execute, _DELETE_TEAM, {"id": team.id}))
