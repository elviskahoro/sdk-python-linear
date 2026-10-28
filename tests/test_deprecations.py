from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from graphql import build_schema

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import deprecations  # noqa: E402

SDL = """
type Query {
  old: Int @deprecated(reason: "use new")
  new(sort: Order, legacy: Int @deprecated(reason: "no")): Int
}
enum Order { a b @deprecated(reason: "gone") }
"""


def test_flags_field_argument_and_enum_value() -> None:
    schema = build_schema(SDL)
    found = deprecations.find_deprecations(
        schema,
        [("op", "query { old new(sort: b, legacy: 1) }")],
    )
    assert [f.split(" (")[0] for f in found] == [
        "op: Order.b",
        "op: Query.legacy",
        "op: Query.old",
    ]


def test_allowlist_suppresses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spec = tmp_path / "_spec.toml"
    spec.write_text('allow_deprecated = ["Query.old"]\n')
    monkeypatch.setattr(deprecations, "SPEC_FILE", spec)
    schema = build_schema(SDL)
    assert deprecations.find_deprecations(schema, [("op", "query { old }")]) == []
