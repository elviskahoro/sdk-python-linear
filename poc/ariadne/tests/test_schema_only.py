"""Schema-only (``enable_custom_operations``) Ariadne client: what it really gives us.

Generated from ``schema/linear.graphql`` alone - no operation documents - with the
stock Ariadne base client, exactly as documented. Behaviour is measured by
``schema_only_probe.py`` inside the generator venv (graphql-core 3.2) because the
generated client does not work on the graphql-core 3.3 this repo pins; that
incompatibility is itself asserted below.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from .conftest import POC_DIR, REPO_ROOT

PROBE = Path(__file__).with_name("schema_only_probe.py")
GENERATOR_PYTHON = POC_DIR / ".venv" / "bin" / "python"


@pytest.fixture(scope="module")
def report(schema_only_package: Path) -> dict[str, Any]:
    assert (schema_only_package / "custom_queries.py").exists()
    result = subprocess.run(
        [str(GENERATOR_PYTHON), str(PROBE)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads(result.stdout)


def test_generated_from_schema_alone(schema_only_package: Path) -> None:
    config = (POC_DIR / "generate.py").read_text()
    assert '"enable_custom_operations": True' in config
    # Whole-schema output: every object type gets a field-builder class.
    sizes = {
        p.name: len(p.read_text().splitlines())
        for p in schema_only_package.glob("*.py")
    }
    assert sizes["custom_fields.py"] > 20_000
    assert sum(sizes.values()) > 50_000


def test_builds_a_valid_document_with_args_aliases_and_fragments(
    report: dict[str, Any],
) -> None:
    success = report["success"]
    assert success["validation_errors"] == []
    assert "items: nodes {" in success["query"]
    assert "... on IssueNotification {" in success["query"]
    assert success["operation_name"] == "Probe"
    assert success["authorization"] == "lin_api_test"
    assert success["variables"] == {
        "filter_0": {"team": {"id": {"eq": "t-1"}}},
        "first_0": 2,
        "orderBy_0": "updatedAt",
        "first_1": 1,
    }


def test_explicit_null_argument_is_silently_dropped(report: dict[str, Any]) -> None:
    # Query.issues(after=None): every builder filters `value is not None`, so a
    # top-level argument can never be sent as null. Nested input fields keep the
    # distinction (model_dump(exclude_unset=True)).
    assert "after" not in report["success"]["query"]
    assert report["mutation"]["variables"]["input_0"] == {
        "assigneeId": None,
        "title": "New",
    }


def test_result_is_an_untyped_dict_keyed_by_wire_names(report: dict[str, Any]) -> None:
    success = report["success"]
    assert success["result_type"] == "dict"
    node = success["result"]["issues"]["items"][0]
    # camelCase wire keys, no snake_case attributes, no datetime/enum parsing.
    assert set(node) == {"id", "identifier", "state", "assignee"}
    assert success["result"]["issues"]["pageInfo"] == {
        "endCursor": "c",
        "hasNextPage": False,
    }


def test_no_generated_typename_selector(schema_only_package: Path) -> None:
    # __typename has to be built by hand: GraphQLField("__typename").
    assert "__typename" not in (schema_only_package / "custom_fields.py").read_text()


def test_stock_error_handling_differs_from_the_sdk_contract(
    report: dict[str, Any],
) -> None:
    # Linear's 400 auth failure: status is checked before `errors`, so the
    # AUTHENTICATION_ERROR code is not surfaced (LinearClient surfaces it).
    assert report["auth_400"]["raised"] == "GraphQLClientHttpError"
    assert report["auth_400"]["codes"] == []
    assert report["errors_no_data"]["codes"] == ["AUTHENTICATION_ERROR"]
    # Partial data is kept on the exception (LinearClient discards it).
    assert report["partial"]["partial_data"] == {"viewer": {"id": "u"}}
    # A spec-violating errors[] entry escapes as a bare KeyError.
    assert report["malformed_error"] == {"raised": "KeyError", "in_hierarchy": False}
    assert report["http_502"]["status_code"] == 502
    assert report["not_json"]["raised"] == "GraphQLClientInvalidResponseError"
    assert report["no_data_key"]["raised"] == "GraphQLClientInvalidResponseError"
    # Stock httpx client: 5 s timeout (LinearClient defaults to 30 s).
    assert report["success"]["default_timeout"] == 5.0


def test_runtime_needs_graphql_core_below_3_3(schema_only_package: Path) -> None:
    """The root venv's graphql-core 3.3 cannot print the builder's documents."""
    assert schema_only_package.exists()
    graphql = importlib.import_module("graphql")
    assert graphql.version_info >= (3, 3)
    client_module = importlib.import_module(
        "poc.ariadne.build.linear_schema_only.client",
    )
    queries = importlib.import_module(
        "poc.ariadne.build.linear_schema_only.custom_queries",
    )
    fields = importlib.import_module(
        "poc.ariadne.build.linear_schema_only.custom_fields",
    )
    client = client_module.LinearSchemaOnlyClient(url="https://example.invalid")
    built = queries.Query.viewer().fields(fields.UserFields.id)
    selections = client._build_selection_set((built,))
    document = client._build_operation_ast(
        selections,
        client_module.OperationType.QUERY,
        "Viewer",
        [],
    )
    with pytest.raises(TypeError, match="Invalid AST Node"):
        graphql.print_ast(document)
