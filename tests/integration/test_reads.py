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


async def test_team_lookup(queries: LinearQueries, team) -> None:
    by_id = await queries.get_team(team.id)
    assert by_id is not None
    assert by_id.key == team.key
    assert await queries.get_team_by_key("ZZZNOPE") is None


async def test_issue_not_found_is_none(queries: LinearQueries) -> None:
    assert await queries.get_issue("ZZZ-999999") is None


async def test_workflow_states(queries: LinearQueries, team) -> None:
    states = await queries.list_workflow_states(team.id)
    assert states
    # Teams can hold several states of one type (e.g. two `started`), and the
    # by-type lookup rejects ambiguity, so probe a type that occurs once.
    types = [s.type for s in states]
    unique = next(t for t in types if types.count(t) == 1)
    resolved = await queries.get_workflow_state_by_type(team.id, unique)
    assert resolved.type == unique


async def test_list_issues_and_page(queries: LinearQueries, team) -> None:
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


async def test_pagination_has_no_duplicates(queries: LinearQueries, team) -> None:
    ids = [i.id async for i in queries.iter_team_issues(team.id, page_size=2, limit=7)]
    assert len(ids) == len(set(ids))


async def test_sub_connections(queries: LinearQueries, team) -> None:
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


async def test_async_iter_workflow_states(queries: LinearQueries, team) -> None:
    ids = [s.id async for s in queries.iter_workflow_states(team.id, page_size=1)]
    assert ids
    assert len(ids) == len(set(ids))


def test_sync_facade(workflow: LinearWorkflow, team) -> None:
    team = workflow.get_team_by_key(team.key)
    assert team is not None
    ids = [i.id for i in workflow.iter_team_issues(team.id, page_size=2, limit=5)]
    assert len(ids) == len(set(ids))
    assert workflow.get_viewer().id
    # The sync wrappers each drive their own event loop, so exercise more than
    # one per facade instance: a wrapper that closed the loop would break here.
    states = workflow.list_workflow_states(team.id)
    assert states
    types = [s.type for s in states]
    unique = next(t for t in types if types.count(t) == 1)
    assert workflow.get_workflow_state_by_type(team.id, unique).type == unique
    page = workflow.list_issues_page({"team": {"id": {"eq": team.id}}}, first=2)
    assert len(page.nodes) <= 2
    assert isinstance(workflow.search_issues("seed").nodes, list)


async def test_pagination_spans_multiple_pages(queries: LinearQueries, team) -> None:
    # The team fixture seeds 3 issues, so page_size=1 forces several cursors.
    one_page = await queries.list_issues_page(
        {"team": {"id": {"eq": team.id}}},
        first=1,
    )
    assert len(one_page.nodes) == 1
    assert one_page.page_info.has_next_page
    ids = [i.id async for i in queries.iter_team_issues(team.id, page_size=1)]
    assert len(ids) >= 3
    assert len(ids) == len(set(ids))


async def test_iter_search_issues_is_iterable(queries: LinearQueries) -> None:
    hits = [h async for h in queries.iter_search_issues("seed", page_size=2, limit=3)]
    assert len(hits) <= 3


def test_sync_open_team_issues(workflow: LinearWorkflow, team) -> None:
    issues = workflow.list_open_team_issues(team.id, first=5)
    assert len(issues) <= 5


# A well-formed UUID that no Linear entity owns: `get_team`/`get_user` only
# return None for it if Linear's wording still matches `looks_like_not_found`;
# these tests are the live pins for the Team and User wording. A rewording
# upstream raises here instead of silently turning the
# documented `-> ... | None` contract into an exception.
_ABSENT_ID = "00000000-0000-4000-8000-000000000000"


async def test_unknown_team_id_is_none(queries: LinearQueries) -> None:
    assert await queries.get_team(_ABSENT_ID) is None


async def test_unknown_user_id_is_none(queries: LinearQueries) -> None:
    assert await queries.get_user(_ABSENT_ID) is None


async def test_iter_issues_follows_filter(queries: LinearQueries, team) -> None:
    """The filter-taking iterator, as opposed to the team-id convenience wrapper."""
    ids = [
        i.id
        async for i in queries.iter_issues(
            {"team": {"id": {"eq": team.id}}},
            page_size=1,
            limit=3,
        )
    ]
    assert len(ids) == 3
    assert len(ids) == len(set(ids))


def test_sync_filter_and_search_iterators(workflow: LinearWorkflow, team) -> None:
    """The sync `iter_*` wrappers the facade adds on top of the async ones.

    `test_sync_facade` covers `iter_team_issues`; these two drive their own
    event loop through `_collect`, so they are the pins for the materializing
    path (every page fetched before the first yield).
    """
    ids = [
        i.id
        for i in workflow.iter_issues(
            {"team": {"id": {"eq": team.id}}},
            page_size=1,
            limit=3,
        )
    ]
    assert len(ids) == 3
    assert len(ids) == len(set(ids))
    hits = [h.id for h in workflow.iter_search_issues("seed", page_size=2, limit=3)]
    assert len(hits) == len(set(hits))


def test_sync_single_entity_lookups(workflow: LinearWorkflow, team) -> None:
    """`get_issue`/`get_user`/`get_team`/`list_issues` through the sync facade."""
    issues = workflow.list_issues(team.id, first=3)
    assert issues
    found = workflow.get_issue(issues[0].id)
    assert found is not None
    assert found.identifier == issues[0].identifier
    assert workflow.get_issue(_ABSENT_ID) is None
    assert workflow.get_team(team.id) is not None
    viewer = workflow.get_viewer()
    user = workflow.get_user(viewer.id)
    assert user is not None
    assert user.id == viewer.id


def test_sync_workflow_states_page(workflow: LinearWorkflow, team) -> None:
    page = workflow.list_workflow_states_page(team.id, first=1)
    assert len(page.nodes) == 1
    assert page.page_info.has_next_page
    second = workflow.list_workflow_states_page(
        team.id,
        first=1,
        after=page.page_info.end_cursor,
    )
    assert second.nodes
    assert second.nodes[0].id != page.nodes[0].id
