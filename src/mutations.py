from typing import Any

from .client import LinearClient
from .generated_types import Issue, IssueCreateInput, IssueUpdateInput, User


class LinearMutations:
    """Typed mutation methods for Linear CRUD operations."""

    def __init__(self, client: LinearClient):
        self._client = client

    async def create_issue(self, input: IssueCreateInput) -> Issue:
        """Create a new issue in Linear.

        Args:
            input: IssueCreateInput with title, teamId, and optional description.

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
                    status {
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
        variables: dict[str, Any] = {
            "input": {
                "title": input.title,
                "teamId": input.teamId,
            },
        }
        if input.description is not None:
            variables["input"]["description"] = input.description

        data = await self._client.execute_async(query, variables)
        issue_data = data.get("issueCreate", {}).get("issue")
        if not issue_data:
            raise ValueError("Failed to create issue: API did not return an issue object")

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
                    status {
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
        update_input: dict[str, Any] = {}
        if update.title is not None:
            update_input["title"] = update.title
        if update.description is not None:
            update_input["description"] = update.description

        data = await self._client.execute_async(query, {"id": issue_id, "input": update_input})
        issue_data = data.get("issueUpdate", {}).get("issue")
        if not issue_data:
            raise ValueError(f"Failed to update issue {issue_id}: API did not return an issue object")

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

    def _parse_user(self, data: dict[str, Any] | None) -> User | None:
        if not data:
            return None
        return User(
            id=data["id"],
            name=data["name"],
            email=data.get("email", ""),
            active=data.get("active", False),
        )

    def _parse_issue(self, data: dict[str, Any]) -> Issue:
        return Issue(
            id=data["id"],
            title=data["title"],
            description=data.get("description"),
            identifier=data["identifier"],
            url=data["url"],
            priority=data.get("priority"),
            status=data.get("status", {}).get("name") if data.get("status") else None,
            assignee=self._parse_user(data.get("assignee")),
        )
