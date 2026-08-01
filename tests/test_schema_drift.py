"""Detects Linear changing its schema out from under the pinned copy.

A vendored snapshot cannot detect upstream drift by construction, so this test
refetches. It is marked ``network`` and deselected by default (see ``addopts`` in
pyproject.toml); CI runs it on a schedule. The source is Linear's public SDK repo,
so no API key is involved.

When this fails, refresh the pin and regenerate:

    curl -sL -o schema/linear.graphql \\
      https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql
    uv run python scripts/codegen.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from graphql import build_schema, parse, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import codegen  # noqa: E402

OPERATIONS = codegen._operation_paths()  # noqa: SLF001
SCHEMA_URL = "https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql"


@pytest.mark.network
def test_operations_still_valid_against_upstream_schema() -> None:
    response = httpx.get(SCHEMA_URL, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    upstream = build_schema(response.text)

    fragments = codegen._fragment_definitions()  # noqa: SLF001
    failures: list[str] = []
    for path in OPERATIONS:
        document = parse(codegen._compose(path, fragments))  # noqa: SLF001
        errors = validate(upstream, document)
        failures.extend(f"{path.name}: {e.message}" for e in errors)

    assert not failures, (  # noqa: S101
        "Linear's schema has drifted from the pinned copy:\n"
        + "\n".join(f"    {f}" for f in failures)
    )
