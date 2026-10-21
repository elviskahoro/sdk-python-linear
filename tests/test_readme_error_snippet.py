"""Regression guard: the README error-handling recipe must run as written."""

import asyncio
import re
from pathlib import Path

import httpx
import respx

README = Path(__file__).resolve().parent.parent / "README.md"
API_URL = "https://api.linear.app/graphql"


def _snippet() -> str:
    text = README.read_text()
    match = re.search(r"## Error handling pattern\s+```python\n(.*?)```", text, re.S)
    assert match
    return match.group(1)


def _run(capsys) -> str:
    code = compile(
        _snippet(),
        "README.md",
        "exec",
        flags=__import__("ast").PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    ns = {"key": "lin_api_test"}
    import types

    result = eval(code, ns)  # noqa: S307
    if isinstance(result, types.CoroutineType):
        asyncio.run(result)
    return capsys.readouterr().out


def test_snippet_uses_typed_access() -> None:
    snippet = _snippet()
    assert "err.code" in snippet
    assert "err.message" in snippet
    assert "err.get(" not in snippet


def test_snippet_handles_graphql_error(capsys) -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Authentication required",
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        },
                    ],
                },
            ),
        )
        out = _run(capsys)
    assert "AUTHENTICATION_ERROR Authentication required" in out


def test_snippet_handles_transport_error(capsys) -> None:
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(503, text="down"))
        out = _run(capsys)
    assert "503" in out
