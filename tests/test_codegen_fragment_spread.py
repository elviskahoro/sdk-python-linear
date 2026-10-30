"""Fragment spreads in the operations spec and dependency-first composition (#149)."""

from __future__ import annotations

import sys
from pathlib import Path

from graphql import parse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gtm_linear import User  # noqa: E402
from gtm_linear._generated import fragments  # noqa: E402
from scripts import codegen  # noqa: E402


def test_dependency_order_puts_spread_fragments_first() -> None:
    doc = parse(
        "fragment A on T { x { ...B } }"
        " fragment B on T { y { ...C } }"
        " fragment C on T { z }",
    )
    defs = {d.name.value: d for d in doc.definitions}
    assert codegen._dependency_order({"A", "B", "C"}, defs) == ["C", "B", "A"]


def test_dependency_order_is_deterministic_and_deduplicated() -> None:
    doc = parse(
        "fragment A on T { a { ...C } b { ...B } }"
        " fragment B on T { z }"
        " fragment C on T { z }",
    )
    defs = {d.name.value: d for d in doc.definitions}
    assert codegen._dependency_order({"A", "B", "C"}, defs) == ["B", "C", "A"]


def test_issue_assignee_types_are_the_shared_user_class() -> None:
    for model in (fragments.IssueFields, fragments.IssueSearchResultFields):
        assert "UserFields" in str(model.__annotations__["assignee"])
    assert "UserFields" in str(
        fragments.IssueContextCommentFields.__annotations__["user"],
    )
    assert User is fragments.UserFields


def test_spec_spreads_user_fields_in_graphql_fragments() -> None:
    text = (REPO_ROOT / "operations" / "_fragments.graphql").read_text()
    assert "assignee { ...UserFields }" in text
    assert "user { ...UserFields }" in text
