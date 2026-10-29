"""Live error-contract checks.

Timeout mapping is deliberately absent: provoking one live would mean racing
Linear's response time, so ``tests/test_cli.py`` pins it against respx
instead. Not-found wording is pinned by the live check in ``tests/test_cli.py``
and, for Team and User, by ``test_reads.py``.
"""

from __future__ import annotations

import pytest

from gtm_linear import LinearClient, LinearQueries
from gtm_linear.exceptions import LinearGraphQLError

pytestmark = pytest.mark.network


async def test_bad_key_maps_to_authentication_error() -> None:
    async with LinearClient(api_key="lin_api_invalid") as c:
        with pytest.raises(LinearGraphQLError) as info:
            await LinearQueries(c).get_viewer()
    assert "AUTHENTICATION_ERROR" in info.value.codes


async def test_trailing_newline_key_authenticates(api_key: str) -> None:
    async with LinearClient(api_key=api_key + "\n") as c:
        viewer = await LinearQueries(c).get_viewer()
    assert viewer.id


async def test_invalid_input_maps_to_graphql_error(api_key: str) -> None:
    async with LinearClient(api_key=api_key) as c:
        with pytest.raises(LinearGraphQLError) as info:
            await c.execute("query { viewer { notAField } }")
    assert info.value.codes
