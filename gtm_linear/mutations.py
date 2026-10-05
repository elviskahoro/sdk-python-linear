"""Typed write operations against the Linear GraphQL API.

Input models are generated from Linear's schema, so they carry every field the API
accepts rather than a hand-picked subset. Variables are serialized with
``model_dump(mode="json", by_alias=True, exclude_unset=True)``:

* ``by_alias`` writes camelCase, which is what Linear expects.
* ``mode="json"`` converts dates, datetimes, and enums to JSON-compatible values.
* ``exclude_unset`` distinguishes "leave this alone" from "set this to null". A field
  you never touched is omitted; a field you explicitly set to None is sent as null.
  ``exclude_none`` — the previous behaviour — could not express the second case, so
  clearing a field was impossible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._generated.CreateAttachment import (
    DOCUMENT as CREATE_ATTACHMENT,
    AttachmentCreateInput,
    CreateAttachmentResult,
)
from ._generated.CreateComment import (
    DOCUMENT as CREATE_COMMENT,
    CommentCreateInput,
    CreateCommentResult,
)
from ._generated.CreateIssue import (
    DOCUMENT as CREATE_ISSUE,
    CreateIssueResult,
    IssueCreateInput,
)
from ._generated.CreateIssueRelation import (
    DOCUMENT as CREATE_ISSUE_RELATION,
    CreateIssueRelationResult,
    IssueRelationCreateInput,
    IssueRelationType,
)
from ._generated.DeleteIssue import (
    DOCUMENT as DELETE_ISSUE,
    DeleteIssueResult,
)
from ._generated.UpdateIssue import (
    DOCUMENT as UPDATE_ISSUE,
    IssueUpdateInput,
    UpdateIssueResult,
)

if TYPE_CHECKING:
    from ._generated.fragments import (
        AttachmentFields,
        CommentFields,
        IssueFields,
        IssueRelationFields,
    )
    from .client import LinearClient


def _variables(model: Any) -> dict[str, Any]:  # noqa: ANN401
    """Serialize an input model the way Linear expects it."""
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


class LinearMutations:
    """Typed mutation methods for Linear CRUD operations."""

    def __init__(self, client: LinearClient) -> None:
        """Initialize LinearMutations.

        Args:
            client: LinearClient instance for API access.
        """
        self._client = client

    async def create_issue(self, input_: IssueCreateInput) -> IssueFields:
        """Create a new issue.

        Args:
            input_: The issue to create. ``team_id`` is the only field Linear
                requires.

        Returns:
            The newly created issue.

        Raises:
            ValueError: If the API reports success but returns no issue.
        """
        data = await self._client.execute_async(
            CREATE_ISSUE,
            {"input": _variables(input_)},
        )
        issue = CreateIssueResult.model_validate(data).issue_create.issue
        if issue is None:
            error_msg = "Failed to create issue: API did not return an issue object"
            raise ValueError(error_msg)
        return issue

    async def update_issue(
        self,
        issue_id: str,
        update: IssueUpdateInput,
    ) -> IssueFields:
        """Update an existing issue.

        Only fields you actually set are sent, so a partially-populated input will
        not blank out the rest of the issue. Setting a field to None explicitly
        clears it.

        Args:
            issue_id: The ID of the issue to update.
            update: The fields to change.

        Returns:
            The updated issue.

        Raises:
            ValueError: If the API reports success but returns no issue.
        """
        data = await self._client.execute_async(
            UPDATE_ISSUE,
            {"id": issue_id, "input": _variables(update)},
        )
        issue = UpdateIssueResult.model_validate(data).issue_update.issue
        if issue is None:
            error_msg = (
                f"Failed to update issue {issue_id}: API did not return an issue object"
            )
            raise ValueError(error_msg)
        return issue

    async def delete_issue(self, issue_id: str) -> bool:
        """Archive an issue.

        Args:
            issue_id: The ID of the issue to delete.

        Returns:
            True if Linear reported success.
        """
        data = await self._client.execute_async(DELETE_ISSUE, {"id": issue_id})
        return DeleteIssueResult.model_validate(data).issue_delete.success

    async def create_comment(self, issue_id: str, body: str) -> CommentFields:
        """Comment on an issue.

        Args:
            issue_id: The ID of the issue to comment on.
            body: The comment body, as markdown.

        Returns:
            The newly created comment.

        Raises:
            ValidationError: If Linear violates its own schema —
                ``CommentPayload.comment`` is ``Comment!`` (non-null), so a null
                comment fails model validation rather than reaching this code.
        """
        comment_input = CommentCreateInput(issue_id=issue_id, body=body)
        data = await self._client.execute_async(
            CREATE_COMMENT,
            {"input": _variables(comment_input)},
        )
        return CreateCommentResult.model_validate(data).comment_create.comment

    async def create_attachment(
        self,
        input_: AttachmentCreateInput,
    ) -> AttachmentFields:
        """Create or update an issue attachment for an external URL.

        Linear treats the URL as unique within the issue, so an existing
        attachment with the same URL is updated rather than duplicated.

        Args:
            input_: The issue ID, URL, and title are required by Linear. The
                generated input model validates those fields before the request.

        Returns:
            The created or updated attachment.
        """
        data = await self._client.execute_async(
            CREATE_ATTACHMENT,
            {"input": _variables(input_)},
        )
        return CreateAttachmentResult.model_validate(data).attachment_create.attachment

    async def create_issue_relation(
        self,
        input_: IssueRelationCreateInput,
    ) -> IssueRelationFields:
        """Create a typed relationship between two issues.

        Args:
            input_: The source issue, related issue, and relation type. The
                generated input model validates the required fields.

        Returns:
            The created issue relation, including both related issue identifiers.
        """
        data = await self._client.execute_async(
            CREATE_ISSUE_RELATION,
            {"input": _variables(input_)},
        )
        return CreateIssueRelationResult.model_validate(
            data,
        ).issue_relation_create.issue_relation


__all__ = [
    "AttachmentCreateInput",
    "CommentCreateInput",
    "IssueCreateInput",
    "IssueRelationCreateInput",
    "IssueRelationType",
    "IssueUpdateInput",
    "LinearMutations",
]
