"""Tests that field-argument defaults survive the codegen pipeline (BUG-9).

``_prune`` re-emits each kept field's argument list. It used to discard SDL-declared
argument defaults (rendering only ``name: type``), so the pruned SDL described a
different API shape than Linear's real schema. These tests pin default preservation
with synthetic schemas that exercise every default kind Linear's pinned SDL actually
declares (Int, Boolean, enum) plus the nullable/non-null and default/no-default
combinations.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import codegen  # noqa: E402


def _build(*types: str) -> object:
    from graphql import build_schema

    return build_schema("\n\n".join(types))


def _prune(
    schema: object,
    touched: dict[str, set[str]],
    whole: set[str] | None = None,
) -> str:
    return codegen._prune(schema, touched, whole or set(), set())  # type: ignore[arg-type]


def _prune_query(schema: object, field: str, whole: set[str] | None = None) -> str:
    return _prune(schema, {"Query": {field}}, whole)


# ---------------------------------------------------------------------------
# _prune: SDL re-emission must preserve declared argument defaults.
# ---------------------------------------------------------------------------


def test_prune_preserves_int_default() -> None:
    schema = _build(
        "type Query { issues(first: Int = 50): IssueConnection! }",
        "type IssueConnection { nodes: [Issue!]! }",
        "type Issue { id: ID! }",
    )
    sdl = _prune(
        schema,
        {"Query": {"issues"}, "IssueConnection": {"nodes"}, "Issue": {"id"}},
    )
    assert "issues(first: Int = 50): IssueConnection!" in sdl


def test_prune_preserves_boolean_default() -> None:
    schema = _build(
        "type Query { team(includeArchived: Boolean = false): Team! }",
        "type Team { id: ID! }",
    )
    sdl = _prune(schema, {"Query": {"team"}, "Team": {"id"}})
    assert "team(includeArchived: Boolean = false): Team!" in sdl


def test_prune_preserves_enum_default_on_non_null_argument() -> None:
    schema = _build(
        "enum IdType { general }",
        "type Query { sso(type: IdType! = general): String! }",
    )
    sdl = _prune_query(schema, "sso", whole={"IdType"})
    assert "sso(type: IdType! = general): String!" in sdl


def test_prune_preserves_enum_default_on_nullable_argument() -> None:
    schema = _build(
        "enum Mode { light }",
        "type Query { theme(mode: Mode = light): String! }",
    )
    sdl = _prune_query(schema, "theme", whole={"Mode"})
    assert "theme(mode: Mode = light): String!" in sdl


def test_prune_omits_default_when_none_declared() -> None:
    schema = _build(
        "type Query { issues(first: Int): IssueConnection! }",
        "type IssueConnection { nodes: [Issue!]! }",
        "type Issue { id: ID! }",
    )
    sdl = _prune(
        schema,
        {"Query": {"issues"}, "IssueConnection": {"nodes"}, "Issue": {"id"}},
    )
    assert "issues(first: Int): IssueConnection!" in sdl
    # No `=` inside the argument list.
    assert "=" not in sdl.split("issues(", 1)[1].split(")", 1)[0]


def test_prune_and_sanitize_preserve_default_end_to_end() -> None:
    """The sanitizer must not strip argument defaults from the pruned SDL."""
    schema = _build(
        "enum IdType { general }",
        "type Query { sso(type: IdType! = general): String! }",
    )
    sdl = codegen._sanitize_sdl(_prune_query(schema, "sso", whole={"IdType"}))
    assert "sso(type: IdType! = general): String!" in sdl
