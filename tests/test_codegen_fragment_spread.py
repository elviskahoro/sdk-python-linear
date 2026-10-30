"""Fragment spreads in the operations spec (#149)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gtm_linear import User  # noqa: E402
from gtm_linear._generated import fragments  # noqa: E402


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
