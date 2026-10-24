"""Static-typing probe, checked by pyright in test_typing.py - never executed.

Lines ending in ``# expect-error`` must produce a pyright error; every other line
must be clean. ``reveal_type`` results are asserted by test_typing.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, reveal_type

from gtm_linear import LinearClient, LinearQueries
from poc.ariadne.build.linear_schema_only.custom_fields import (
    IssueConnectionFields,
    IssueFields,
)
from poc.ariadne.build.linear_schema_only.custom_queries import Query
from poc.ariadne.linear_ariadne import LinearAriadneClient

if TYPE_CHECKING:
    from poc.ariadne.build.linear_schema_only.client import LinearSchemaOnlyClient


async def current_sdk(linear: LinearClient) -> None:
    """Baseline: the current hand-wrapped, operation-based SDK."""
    issue = await LinearQueries(linear).get_issue("ENG-1")
    reveal_type(issue)  # current_get_issue
    if issue is not None:
        reveal_type(issue.state.type)  # current_state_type
        _ = issue.assignee.email  # expect-error


async def operation_based(linear: LinearClient) -> None:
    """Mode A: methods generated from operations/*.graphql."""
    client = LinearAriadneClient(linear)
    result = await client.get_issue(id="ENG-1")
    reveal_type(result)  # ariadne_get_issue
    reveal_type(result.issue.state.type)  # ariadne_state_type
    reveal_type(result.issue.assignee)  # ariadne_assignee
    _ = result.issue.assignee.email  # expect-error
    _ = result.issue.no_such_field  # expect-error
    await client.get_issue(id=123)  # expect-error
    await client.list_issues(first="10", include_archived=False)  # expect-error
    page = await client.list_issues(first=10, include_archived=False)
    reveal_type(page.issues.nodes)  # ariadne_nodes
    reveal_type(page.issues.page_info.end_cursor)  # ariadne_cursor


async def schema_only(client: LinearSchemaOnlyClient) -> None:
    """Mode B: the custom-operation builder, no operation documents."""
    built = Query.issues(first=2).fields(
        IssueConnectionFields.nodes().fields(IssueFields.id, IssueFields.title),
    )
    Query.issues(first="2")  # expect-error
    _ = IssueFields.no_such_field  # expect-error
    data = await client.query(built, operation_name="Issues")
    reveal_type(data)  # schema_only_result
    # Nothing below is checked: typos and unselected fields are invisible.
    reveal_type(data["issues"]["nodes"][0]["titel"])  # schema_only_typo
