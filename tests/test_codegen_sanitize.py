"""Non-mutating AST rebuilds and the SDL sanitizer.

graphql-core 3.3 freezes its AST dataclasses, so codegen.py must build
replacement documents instead of mutating parsed nodes in place — the in-place
splice is what raised FrozenInstanceError in 13 CI tests when a fresh
dependency resolution pulled graphql-core 3.3.0. These tests pin the rebuild
semantics (untouched subtrees shared, inputs never mutated) and the sanitizer's
contract, so a future graphql-core bump cannot silently regress either.
"""

from __future__ import annotations

import keyword
import sys
from pathlib import Path
from typing import Any, cast

import graphql.language.ast as gql_ast
from graphql import parse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import codegen  # noqa: E402

# Exercises every sanitizer branch: a directive definition (BUG-1), type and
# field descriptions with an apostrophe (BUG-2), Python-keyword enum values
# used in an input default (BUG-3), and definition kinds in mixed order (BUG-5).
SDL = '''\
directive @cache(ttl: Int!) on FIELD_DEFINITION

"""doc with an apostrophe"""
type Issue {
  """field doc"""
  id: ID
}

enum Keyword {
  """enum doc"""
  continue
  pass
}

input Sort {
  nulls: Keyword = continue
}

scalar Date
'''


def test_rebuild_shares_untouched_subtrees() -> None:
    """An override that changes nothing must return the original node."""
    document = parse("query Q { a b }")
    assert codegen._rebuild(document, lambda _node: {}) is document


def test_rebuild_replaces_fields_without_mutating_inputs() -> None:
    """Rebuilding must leave the tree it was handed exactly as it was."""

    def drop_descriptions(node: gql_ast.Node) -> dict[str, Any]:
        if getattr(node, "description", None) is not None:
            return {"description": None}
        return {}

    document = parse('"""doc"""\ntype Issue { id: ID }')
    definition = document.definitions[0]
    assert isinstance(definition, gql_ast.ObjectTypeDefinitionNode)
    original = definition.description

    rebuilt = cast(
        "gql_ast.DocumentNode",
        codegen._rebuild(document, drop_descriptions),
    )
    rebuilt_definition = rebuilt.definitions[0]
    assert isinstance(rebuilt_definition, gql_ast.ObjectTypeDefinitionNode)

    assert rebuilt is not document
    assert rebuilt_definition.description is None
    # The parsed input keeps its description: pre-3.3 graphql-core nodes are
    # mutable, so only an assertion can hold the line until the <3.3 cap lifts.
    assert definition.description is original


def test_sanitize_sdl_strips_directives_descriptions_and_keyword_enums() -> None:
    sanitized = codegen._sanitize_sdl(SDL)

    assert "directive" not in sanitized  # BUG-1
    assert '"""' not in sanitized  # BUG-2
    for value in ("continue", "pass"):
        assert keyword.iskeyword(value)
        assert f"{value}_" in sanitized  # BUG-3: renamed to be assignable
    # Only the enum *definitions* are renamed; default-value references are
    # left alone, exactly like the original in-place sanitizer. Untraversable
    # with the real Linear schema, whose pruned SDL has no keyword enum values.
    assert "nulls: Keyword = continue" in sanitized


def test_sanitize_sdl_orders_scalars_and_enums_first() -> None:
    """Defaults evaluate at class creation, so their enums must come first."""
    sanitized = codegen._sanitize_sdl(SDL)

    positions = [
        sanitized.index(definition)
        for definition in ("scalar Date", "enum Keyword", "input Sort", "type Issue")
    ]
    assert positions == sorted(positions)  # BUG-5


def test_unwrap_names_the_wrapped_type_and_returns_none_otherwise() -> None:
    """Pin _unwrap's None contract so a refactor cannot change it silently.

    graphql-core never hands _unwrap something that is not a named type once
    unwrapped; the two callers split on None by design — enter_field raises
    SystemExit on it (schema/operation disagreement), close_over_input skips
    it. Pinning the return values here keeps that split intentional rather
    than accidental.
    """
    from graphql import GraphQLList, GraphQLNonNull, GraphQLScalarType

    named = GraphQLScalarType("X")
    assert codegen._unwrap(named) is named
    assert codegen._unwrap(GraphQLNonNull(GraphQLList(named))) is named

    # The two inputs that can never name a type: nothing, and a non-type.
    assert codegen._unwrap(None) is None
    assert codegen._unwrap(object()) is None
