"""Tests that field-argument defaults survive the codegen pipeline (BUG-9).

``_prune`` re-emits each kept field's argument list and ``_restore_field_arguments``
rebuilds argumented fields as resolvers. Both used to discard SDL-declared argument
defaults (``_prune`` rendered only ``name: type``; the rebuild hardcoded ``= None``
for every nullable argument), so the shipped mirror described a different API shape
than Linear's real schema. These tests pin default preservation at both sites with
synthetic schemas that exercise every default kind Linear's pinned SDL actually
declares (Int, Boolean, enum) plus the nullable/non-null and default/no-default
combinations.
"""

from __future__ import annotations

import sys
import textwrap
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


# ---------------------------------------------------------------------------
# _restore_field_arguments: resolver signatures must reflect declared defaults.
# ---------------------------------------------------------------------------


def _restore(sdl: str, tmp_path: Path) -> str:
    """Run _restore_field_arguments over a minimal Query-only schema module."""
    sdl_path = tmp_path / "pruned.graphql"
    sdl_path.write_text(sdl)
    source = textwrap.dedent("""\
        import strawberry


        @strawberry.type
        class Query:
            placeholder: str
        """)
    return codegen._restore_field_arguments(source, sdl_path)


def test_restore_renders_int_default(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        type Query {
          issues(first: Int = 50): IssueConnection!
        }

        type IssueConnection {
          nodes: [Issue!]!
        }

        type Issue {
          id: ID!
        }
        """)
    result = _restore(sdl, tmp_path)
    assert "first: int | None = 50," in result
    assert "first: int | None = None," not in result


def test_restore_renders_boolean_default(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        type Query {
          team(includeArchived: Boolean = false): Team!
        }

        type Team {
          id: ID!
        }
        """)
    result = _restore(sdl, tmp_path)
    assert "include_archived: bool | None = False," in result


def test_restore_renders_enum_default_on_non_null_argument(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        enum IdType {
          general
        }

        type Query {
          sso(type: IdType! = general): String!
        }
        """)
    result = _restore(sdl, tmp_path)
    # Non-null annotation stays non-null, but the declared default is rendered.
    assert "type: IdType = IdType.general," in result
    assert "type: IdType | None" not in result


def test_restore_renders_enum_default_on_nullable_argument(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        enum Mode {
          light
        }

        type Query {
          theme(mode: Mode = light): String!
        }
        """)
    result = _restore(sdl, tmp_path)
    assert "mode: Mode | None = Mode.light," in result


def test_restore_falls_back_to_none_for_nullable_without_default(
    tmp_path: Path,
) -> None:
    sdl = textwrap.dedent("""\
        type Query {
          issues(first: Int): IssueConnection!
        }

        type IssueConnection {
          nodes: [Issue!]!
        }

        type Issue {
          id: ID!
        }
        """)
    result = _restore(sdl, tmp_path)
    assert "first: int | None = None," in result


def test_restore_keeps_required_for_non_null_without_default(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        type Query {
          issue(id: String!): Issue!
        }

        type Issue {
          id: ID!
        }
        """)
    result = _restore(sdl, tmp_path)
    # Required argument: no default fragment, just the annotation.
    assert "id: str," in result
    assert "id: str = " not in result


def test_restore_orders_required_before_optional_with_defaults(tmp_path: Path) -> None:
    sdl = textwrap.dedent("""\
        enum IdType {
          general
        }

        type Query {
          sso(type: IdType! = general, first: Int = 50, after: String): String!
        }
        """)
    result = _restore(sdl, tmp_path)
    lines = [line.strip() for line in result.splitlines()]
    # Required (type) first, then optionals in SDL order.
    assert lines.index("type: IdType = IdType.general,") < lines.index(
        "first: int | None = 50,",
    )
    assert lines.index("first: int | None = 50,") < lines.index(
        "after: str | None = None,",
    )


def test_restore_renames_python_keyword_enum_default(tmp_path: Path) -> None:
    """An enum default referencing a Python-keyword value uses the BUG-3 rename."""
    sdl = textwrap.dedent("""\
        enum Mode {
          continue
        }

        type Query {
          next(mode: Mode = continue): String!
        }
        """)
    result = _restore(sdl, tmp_path)
    # The enum member is renamed to `continue_` (BUG-3); the default reference
    # must point at the renamed member or the emitted module would not parse.
    assert "mode: Mode | None = Mode.continue_," in result


def test_pipeline_preserves_default_through_prune_and_restore(tmp_path: Path) -> None:
    """End-to-end through both bug sites: default survives prune + restore."""
    schema = _build(
        "enum IdType { general }",
        "type Query { sso(type: IdType! = general): String! }",
    )
    sdl = codegen._sanitize_sdl(_prune_query(schema, "sso", whole={"IdType"}))
    result = _restore(sdl, tmp_path)
    assert "type: IdType = IdType.general," in result


def test_restore_threads_field_descriptions(tmp_path: Path) -> None:
    sdl = textwrap.dedent('''\
        type Query {
          "He said \\"hi\\" and it's fine"
          issues(first: Int): Int!
          "plain doc"
          count: Int!
        }
        ''')
    result = _restore(sdl, tmp_path)
    assert "He said" in result
    assert "count: int = strawberry.field(description='plain doc')" in result
