"""Shared fixtures for the live-API integration suite.

Reads need ``LINEAR_API_KEY`` and ``LINEAR_IT_TEAM_KEY`` (a dedicated test team).
Writes additionally carry the ``network_writes`` marker and run only in the
mutating RWX task. Everything skips, never fails, when the key is absent.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TYPE_CHECKING, TypeVar

import pytest
import pytest_asyncio

from gtm_linear import (
    LinearClient,
    LinearMutations,
    LinearQueries,
    LinearWorkflow,
)
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
def team_key() -> str:
    key = os.environ.get("LINEAR_IT_TEAM_KEY")
    if not key:
        pytest.skip("LINEAR_IT_TEAM_KEY not set (dedicated integration test team)")
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


@pytest_asyncio.fixture
async def team(queries: LinearQueries, team_key: str):  # noqa: ANN201
    found = await queries.get_team_by_key(team_key)
    if found is None:
        pytest.fail(f"integration team {team_key!r} not found for this key")
    return found
