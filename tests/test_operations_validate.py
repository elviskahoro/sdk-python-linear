"""Every SDK operation must be valid against Linear's real schema.

Catches the failure mode that used to surface as a runtime KeyError deep in a parser:
selecting a field Linear does not have, or passing a variable of the wrong type.

Operations are validated in their composed form — with the shared fragments from
``operations/_fragments.graphql`` spliced in — which is exactly what codegen and the
wire see. Validating the raw files would fail on the unresolved fragment spreads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from graphql import GraphQLSchema, build_schema, parse, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import codegen  # noqa: E402

OPERATIONS = codegen._operation_paths()


@pytest.fixture(scope="session")
def linear_schema() -> GraphQLSchema:
    """Parse the pinned SDL once; it is ~1.3 MB and parsing dominates the runtime."""
    return build_schema((REPO_ROOT / "schema" / "linear.graphql").read_text())


def test_operations_exist() -> None:
    assert OPERATIONS, "no operation documents found"


@pytest.mark.parametrize("path", OPERATIONS, ids=lambda p: p.stem)
def test_operation_validates(path: Path, linear_schema: GraphQLSchema) -> None:
    fragments = codegen._fragment_definitions()
    document = parse(codegen._compose(path, fragments))
    errors = validate(linear_schema, document)
    assert not errors, "\n".join(e.message for e in errors)
