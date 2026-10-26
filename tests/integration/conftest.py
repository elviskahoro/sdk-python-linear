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
import uuid
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
            if "RATELIMITED" not in exc.codes and "RATE_LIMIT_ERROR" not in exc.codes:
                raise
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)
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
_CREATE_TEAM = (
    "mutation($i: TeamCreateInput!) { teamCreate(input: $i) { team { id name key } } }"
)
_DELETE_TEAM = "mutation($id: String!) { teamDelete(id: $id) { success } }"
_LIST_TEAMS = "{ teams(first: 100) { nodes { id name } } }"


@pytest.fixture(scope="session")
def team(api_key: str, run_id: str) -> Iterator[TeamFields]:
    """A disposable team created for this run, seeded with issues, deleted after.

    The suite needs only ``LINEAR_API_KEY``: it builds the team it tests
    against, so no pre-provisioned workspace state is assumed. Teams leaked by
    crashed earlier runs (name prefix ``sdk-it``) are swept first. Because the
    suite only ever deletes teams it created or recognises by that prefix, point
    it at a disposable workspace.
    """
    with LinearClient(api_key=api_key) as client:
        for node in client.execute(_LIST_TEAMS)["teams"]["nodes"]:
            if node["name"].startswith(TEAM_NAME_PREFIX):
                client.execute(_DELETE_TEAM, {"id": node["id"]})
        letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
        created = client.execute(
            _CREATE_TEAM,
            {"i": {"name": f"{TEAM_NAME_PREFIX} {run_id}", "key": f"IT{letters}"}},
        )["teamCreate"]["team"]
        team = TeamFields(**created)
        try:
            with LinearWorkflow(api_key) as wf:
                for n in range(3):
                    wf.create_issue(
                        IssueCreateInput(title=f"seed {n}", team_id=team.id),
                    )
            yield team
        finally:
            client.execute(_DELETE_TEAM, {"id": team.id})
