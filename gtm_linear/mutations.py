from dataclasses import fields
from typing import Any

import strawberry
from strawberry.types.maybe import Some

from .client import LinearClient
from .generated_types import Issue, IssueCreateInput, IssueUpdateInput, User


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
            input_: Strawberry IssueCreateInput containing the issue fields to set.

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
        variables: dict[str, Any] = {"input": self._serialize_input(input_)}

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
            update: Strawberry IssueUpdateInput containing the fields to change.

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
        data = await self._client.execute_async(
            query,
            {"id": issue_id, "input": self._serialize_input(update)},
        )
        issue_data = data.get("issueUpdate", {}).get("issue")
        if not issue_data:
            error_msg = (
                f"Failed to update issue {issue_id}: API did not return an issue object"
            )
            raise ValueError(error_msg)

        return self._parse_issue(issue_data)

    @staticmethod
    def _serialize_input(input_: IssueCreateInput | IssueUpdateInput) -> dict[str, Any]:
        """Convert a Strawberry input instance to GraphQL variables.

        Strawberry input types are dataclasses, so their declared fields remain the
        single source of truth for the payload. Strawberry's Maybe type allows an
        omitted field to differ from an explicit Some(None), which is preserved for
        callers that need to clear a nullable Linear field.
        """
        payload = strawberry.asdict(input_)
        for field in fields(input_):
            value = getattr(input_, field.name)
            if isinstance(value, Some):
                payload[field.name] = value.value
        return payload

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
        """Parse user data from API response.

        Args:
            data: User data dictionary from API.

        Returns:
            User instance or None if data is None.
        """
        if not data:
            return None
        return User(
            id=data["id"],  # type: ignore[call-arg]
            name=data["name"],  # type: ignore[call-arg]
            email=data.get("email", ""),  # type: ignore[call-arg]
            active=data.get("active", False),  # type: ignore[call-arg]
        )

    def _parse_issue(self, data: dict[str, Any]) -> Issue:
        """Parse issue data from API response.

        Args:
            data: Issue data dictionary from API.

        Returns:
            Issue instance.
        """
        return Issue(
            id=data["id"],  # type: ignore[call-arg]
            title=data["title"],  # type: ignore[call-arg]
            description=data.get("description"),  # type: ignore[call-arg]
            identifier=data["identifier"],  # type: ignore[call-arg]
            url=data["url"],  # type: ignore[call-arg]
            priority=data.get("priority"),  # type: ignore[call-arg]
            status=data.get("status", {}).get("name") if data.get("status") else None,  # type: ignore[call-arg]
            assignee=self._parse_user(data.get("assignee")),  # type: ignore[call-arg]
        )
