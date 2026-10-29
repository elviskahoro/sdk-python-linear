"""Mutating issue lifecycle. Needs ``LINEAR_API_KEY``."""

from __future__ import annotations

import pytest

from gtm_linear import (
    AttachmentCreateInput,
    IssueCreateInput,
    IssueUpdateInput,
    LinearMutations,
    LinearQueries,
)
from gtm_linear._generated.CreateIssueRelation import (
    IssueRelationCreateInput,
    IssueRelationType,
)
from tests.integration.conftest import with_backoff

pytestmark = [pytest.mark.network, pytest.mark.network_writes]


async def test_issue_lifecycle(
    queries: LinearQueries,
    mutations: LinearMutations,
    team,
    title_prefix: str,
) -> None:
    created: list[str] = []
    try:
        a = await with_backoff(
            lambda: mutations.create_issue(
                IssueCreateInput(title=f"{title_prefix} a", team_id=team.id),
            ),
        )
        created.append(a.id)
        b = await with_backoff(
            lambda: mutations.create_issue(
                IssueCreateInput(title=f"{title_prefix} b", team_id=team.id),
            ),
        )
        created.append(b.id)
        assert a.title == f"{title_prefix} a"

        updated = await mutations.update_issue(
            a.id,
            IssueUpdateInput(title=f"{title_prefix} renamed"),
        )
        assert updated.title == f"{title_prefix} renamed"

        comment = await mutations.create_comment(a.id, "sdk-it comment")
        comments = [c async for c in queries.iter_issue_comments(a.id)]
        assert comment.id in {c.id for c in comments}

        attachment = await mutations.create_attachment(
            AttachmentCreateInput(
                issue_id=a.id,
                title="sdk-it",
                url="https://example.com/sdk-it",
            ),
        )
        attachments = [x async for x in queries.iter_issue_attachments(a.id)]
        assert attachment.id in {x.id for x in attachments}

        relation = await mutations.create_issue_relation(
            IssueRelationCreateInput(
                issue_id=a.id,
                related_issue_id=b.id,
                type=IssueRelationType.related,
            ),
        )
        relations = [r async for r in queries.iter_issue_relations(a.id)]
        assert relation.id in {r.id for r in relations}
        inverse = [r async for r in queries.iter_issue_inverse_relations(b.id)]
        assert relation.id in {r.id for r in inverse}
    finally:
        for issue_id in created:
            await mutations.delete_issue(issue_id)
