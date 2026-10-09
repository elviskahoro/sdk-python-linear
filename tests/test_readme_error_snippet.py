"""Regression guard: the README "Error handling pattern" snippet must not crash.

PR #13 replaced the raw ``list[dict]`` entries in ``LinearAPIError.errors`` with
typed ``GraphQLError`` pydantic models, but the README recipe kept calling the
old dict ``.get(...)`` API on what are now models, so copied user code crashed
with ``AttributeError: 'GraphQLError' object has no attribute 'get'`` on the
first real GraphQL error instead of branching on the code. The fix updated the
README prose (``README.md`` "Error contract") and the recipe (``README.md``
"Error handling pattern") to the typed contract.

These tests execute the README recipe verbatim against mocked responses so the
README cannot drift from ``gtm_linear/exceptions.py`` again: if ``.get(...)``
(or any other stale dict API) is reintroduced, the snippet raises
``AttributeError`` inside its ``except`` block and the test fails. Existing
README coverage in ``tests/test_queries.py`` only substring-greps for obsolete
names; nothing executed a snippet, which is how the drift went undetected.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import respx

from gtm_linear.client import LinearClient

if TYPE_CHECKING:
    import pytest

API_URL = LinearClient.BASE_URL
README = Path(__file__).parent.parent / "README.md"


def _extract_error_handling_snippet() -> str:
    """Return the verbatim python source under "## Error handling pattern".

    Pinning the recipe by its header (not by line numbers) keeps the guard
    viable as surrounding prose is edited; the recipe block is the only
    ``python``` fence directly under that header.
    """
    text = README.read_text()
    match = re.search(
        r"^## Error handling pattern\n+```python\n(.*?)\n```",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "README 'Error handling pattern' snippet not found"
    return match.group(1)


def _run_readme_error_snippet() -> str:
    """Execute the README recipe verbatim and return its captured stdout.

    The recipe is async (``async with LinearClient`` / ``await execute_async``)
    and references an undefined ``key`` variable; we compile it with
    ``PyCF_ALLOW_TOP_LEVEL_AWAIT`` so the module body becomes a coroutine,
    inject ``key``, and run it under ``asyncio.run``. A ``respx.mock`` context
    must already be active — the recipe fires a real ``httpx`` request whose
    response the caller has already mocked.
    """
    snippet = _extract_error_handling_snippet()
    namespace: dict[str, object] = {"key": "lin_api_test"}
    code = compile(
        snippet,
        str(README),
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    result = eval(code, namespace)  # noqa: S307 - README snippet, not untrusted input
    if inspect.iscoroutine(result):
        asyncio.run(result)
    return ""


def test_readme_error_snippet_uses_typed_graphqlerror_access() -> None:
    """The recipe must use ``GraphQLError`` attribute access, not dict ``.get``.

    A static, fast-failing guard documenting the typed contract: ``exc.errors``
    entries are ``GraphQLError`` models exposing ``.code`` / ``.message`` /
    ``.extensions``. Calling ``.get(...)`` on the model itself (the bug from
    PR #13) raises ``AttributeError``; calling ``.get`` on the *dict*
    ``err.extensions`` is still valid, so the guard keys on ``err.get(`` — the
    exact stale-dict access pattern — not on every ``.get``.
    """
    snippet = _extract_error_handling_snippet()
    assert "err.get(" not in snippet, (
        "README error-handling snippet must use typed GraphQLError attribute "
        "access (err.code, err.message), not dict err.get(...); see "
        "gtm_linear/exceptions.py"
    )
    assert "err.code" in snippet
    assert "err.message" in snippet


def test_readme_error_snippet_handles_graphql_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented handler must inspect a GraphQL error without raising.

    Mirrors ``test_graphql_error_exposes_linear_error_code`` but executes the
    README recipe verbatim against the same mocked response. Before the fix
    this branch raised ``AttributeError: 'GraphQLError' object has no attribute
    'get'`` out of the ``except`` block; the recipe must now print the code and
    message of each structured error.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "You need to authenticate",
                            "path": ["viewer"],
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        },
                    ],
                },
            ),
        )
        _run_readme_error_snippet()

    out = capsys.readouterr().out
    assert "GraphQL error: You need to authenticate" in out
    assert "AUTHENTICATION_ERROR" in out
    assert "You need to authenticate" in out


def test_readme_error_snippet_handles_transport_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Transport failures (``LinearHTTPError``) leave ``exc.errors`` empty.

    The recipe's comment claims "Both transport and GraphQL errors land here";
    the loop body must therefore be a no-op for transport errors (``exc.errors
    == []``) rather than crashing or printing a spurious error code. This is
    the branch that previously masked the ``.get`` bug in smoke tests — the
    loop never ran, so the crash never surfaced.
    """
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(503, text="boom"))
        _run_readme_error_snippet()

    out = capsys.readouterr().out
    assert "HTTP error: 503" in out
    # No structured error entries exist on the transport branch; the recipe's
    # per-error loop prints nothing, so no code leaks into the output.
    assert "AUTHENTICATION_ERROR" not in out
    assert "FORBIDDEN" not in out
    assert "RATELIMITED" not in out
