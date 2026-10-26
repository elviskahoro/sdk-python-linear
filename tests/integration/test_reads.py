"""Read-only live checks (``network`` marker, no mutations)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from gtm_linear import LinearQueries, LinearWorkflow

pytestmark = pytest.mark.network


async def test_viewer_and_user(queries: LinearQueries) -> None:
    viewer = await queries.get_viewer()
    assert viewer.id
    assert viewer.email
    again = await queries.get_user(viewer.id)
    assert again is not None
    assert again.id == viewer.id


async def test_team_lookup(queries: LinearQueries, team) -> None:  # noqa: ANN001
    by_id = await queries.get_team(team.id)
    assert by_id is not None
    assert by_id.key == team.key
    assert await queries.get_team_by_key("ZZZNOPE") is None


async def test_issue_not_found_is_none(queries: LinearQueries) -> None:
    assert await queries.get_issue("ZZZ-999999") is None


async def test_workflow_states(queries: LinearQueries, team) -> None:  # noqa: ANN001
    states = await queries.list_workflow_states(team.id)
    assert states
    first = states[0]
    resolved = await queries.get_workflow_state_by_type(team.id, first.type)
    assert resolved.type == first.type


async def test_list_issues_and_page(queries: LinearQueries, team) -> None:  # noqa: ANN001
    issues = await queries.list_issues(team.id, first=3)
    assert len(issues) <= 3
    page = await queries.list_issues_page({"team": {"id": {"eq": team.id}}}, first=2)
    assert len(page.nodes) <= 2
    if issues:
        found = await queries.get_issue(issues[0].id)
        assert found is not None
        assert found.identifier == issues[0].identifier
        search = await queries.search_issues(issues[0].title[:40])
        # Linear's search index lags writes, so a fresh issue may not hit yet.
        assert isinstance(search.nodes, list)


async def test_pagination_has_no_duplicates(queries: LinearQueries, team) -> None:  # noqa: ANN001
    ids = [i.id async for i in queries.iter_team_issues(team.id, page_size=2, limit=7)]
    assert len(ids) == len(set(ids))


async def test_sub_connections(queries: LinearQueries, team) -> None:  # noqa: ANN001
    issues = await queries.list_issues(team.id, first=1)
    if not issues:
        pytest.skip("test team has no issues")
    issue_id = issues[0].id
    for it in (
        queries.iter_issue_comments,
        queries.iter_issue_attachments,
        queries.iter_issue_relations,
        queries.iter_issue_inverse_relations,
    ):
        nodes = [n async for n in it(issue_id, page_size=1, limit=3)]
        assert len(nodes) <= 3


async def test_async_iter_workflow_states(queries: LinearQueries, team) -> None:  # noqa: ANN001
    ids = [s.id async for s in queries.iter_workflow_states(team.id, page_size=1)]
    assert ids
    assert len(ids) == len(set(ids))


def test_sync_facade(workflow: LinearWorkflow, team) -> None:
    team = workflow.get_team_by_key(team.key)
    assert team is not None
    ids = [i.id for i in workflow.iter_team_issues(team.id, page_size=2, limit=5)]
    assert len(ids) == len(set(ids))
    assert workflow.get_viewer().id
