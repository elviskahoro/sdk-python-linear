from typing import Any

from .client import LinearClient
from .generated_types import Comment, Issue, IssueCreateInput, IssueUpdateInput, User
from .models import (
    CommentCreateInputModel,
    CommentModel,
    IssueModel,
    UserModel,
)


class LinearMutations:
    """Typed mutation methods for Linear CRUD operations."""

    def __init__(self, client: LinearClient) -> None:
        """Initialize LinearMutations.

        Args:
            client: LinearClient instance for API access.
        """
        self._client = client

    async def create_issue(self, input_: IssueCreateInput) -> Issue:
        """Create a new issue in Linear.

        Args:
            input_: IssueCreateInput with title, teamId, and optional description.

        Returns:
            The newly created Issue.

        Raises:
            ValueError: If the API response does not contain an issue.
            LinearAPIError: If the API request fails.
        """
        query = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    title
                    description
                    identifier
                    url
                    priority
                    status: state {
                        name
                    }
                    assignee {
                        id
                        name
                        email
                        active
                    }
                }
            }
        }
        """
        input_model = input_.to_pydantic()
        variables: dict[str, Any] = {
            "input": input_model.model_dump(exclude_none=True),
        }

        data = await self._client.execute_async(query, variables)
        issue_data = data.get("issueCreate", {}).get("issue")
        if not issue_data:
            error_msg = "Failed to create issue: API did not return an issue object"
            raise ValueError(error_msg)

        return self._parse_issue(issue_data)

    async def update_issue(self, issue_id: str, update: IssueUpdateInput) -> Issue:
        """Update an existing issue in Linear.

        Args:
            issue_id: The ID of the issue to update.
            update: IssueUpdateInput with optional title and description.

        Returns:
            The updated Issue.

        Raises:
            ValueError: If the API response does not contain an issue.
            LinearAPIError: If the API request fails.
        """
        query = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue {
                    id
                    title
                    description
                    identifier
                    url
                    priority
                    status: state {
                        name
                    }
                    assignee {
                        id
                        name
                        email
                        active
                    }
                }
            }
        }
        """
        update_input = update.to_pydantic().model_dump(exclude_none=True)

        data = await self._client.execute_async(
            query,
            {"id": issue_id, "input": update_input},
        )
        issue_data = data.get("issueUpdate", {}).get("issue")
        if not issue_data:
            error_msg = (
                f"Failed to update issue {issue_id}: API did not return an issue object"
            )
            raise ValueError(error_msg)

        return self._parse_issue(issue_data)

    async def delete_issue(self, issue_id: str) -> bool:
        """Delete an issue in Linear.

        Args:
            issue_id: The ID of the issue to delete.

        Returns:
            True if the deletion was successful, False otherwise.

        Raises:
            LinearAPIError: If the API request fails.
        """
        query = """
        mutation DeleteIssue($id: String!) {
            issueDelete(id: $id) {
                success
            }
        }
        """
        data = await self._client.execute_async(query, {"id": issue_id})
        return bool(data.get("issueDelete", {}).get("success", False))

    async def create_comment(self, issue_id: str, body: str) -> Comment:
        """Create a comment on an issue in Linear.

        Args:
            issue_id: The ID of the issue to comment on.
            body: The comment body.

        Returns:
            The newly created Comment.

        Raises:
            ValueError: If the API response does not contain a comment.
            LinearAPIError: If the API request fails.
        """
        query = """
        mutation CreateComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment {
                    id
                    body
                    url
                    createdAt
                }
            }
        }
        """
        comment_input = CommentCreateInputModel.model_validate(
            {"issueId": issue_id, "body": body},
        )
        data = await self._client.execute_async(
            query,
            {"input": comment_input.model_dump()},
        )
        comment_data = data.get("commentCreate", {}).get("comment")
        if not comment_data:
            error_msg = "Failed to create comment: API did not return a comment object"
            raise ValueError(error_msg)

        return self._parse_comment(comment_data)

    def _parse_user(self, data: dict[str, Any] | None) -> User | None:
        """Parse user data from API response.

        Args:
            data: User data dictionary from API.

        Returns:
            User instance or None if data is None.
        """
        if not data:
            return None
        return User.from_pydantic(UserModel.model_validate(data))

    def _parse_issue(self, data: dict[str, Any]) -> Issue:
        """Parse issue data from API response.

        Args:
            data: Issue data dictionary from API.

        Returns:
            Issue instance.
        """
        normalized_data = dict(data)
        status = normalized_data.get("status")
        normalized_data["status"] = status.get("name") if status else None
        if normalized_data.get("assignee"):
            normalized_data["assignee"] = UserModel.model_validate(
                normalized_data["assignee"],
            )
        issue_model = IssueModel.model_validate(normalized_data)
        return Issue.from_pydantic(issue_model)

    def _parse_comment(self, data: dict[str, Any]) -> Comment:
        """Parse comment data from an API response."""
        try:
            comment_model = CommentModel.model_validate(data)
        except (TypeError, ValueError) as exc:
            raise ValueError("Failed to parse comment response") from exc
        return Comment.from_pydantic(comment_model)
