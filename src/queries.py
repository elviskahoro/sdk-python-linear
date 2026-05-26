from typing import Any

from .client import LinearClient
from .generated_types import Issue, Team, User


class LinearQueries:
    """Typed query methods for common Linear read operations."""

    def __init__(self, client: LinearClient) -> None:
        """Initialize LinearQueries.

        Args:
            client: LinearClient instance for API access.
        """
        self._client = client

    async def get_issue(self, issue_id: str) -> Issue | None:
        """Fetch a single issue by ID.

        Args:
            issue_id: The Linear issue ID.

        Returns:
            An Issue instance, or None if not found.
        """
        query = """
        query GetIssue($id: String!) {
            issue(id: $id) {
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
        """
        data = await self._client.execute_async(query, {"id": issue_id})
        issue_data = data.get("issue")
        if not issue_data:
            return None
        return self._parse_issue(issue_data)

    async def list_issues(self, team_id: str, first: int = 50) -> list[Issue]:
        """List issues for a team.

        Args:
            team_id: The Linear team ID.
            first: Maximum number of issues to return (default 50).

        Returns:
            A list of Issue instances.
        """
        query = """
        query ListIssues($teamId: String!, $first: Int!) {
            team(id: $teamId) {
                issues(first: $first) {
                    nodes {
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
        }
        """
        data = await self._client.execute_async(
            query,
            {"teamId": team_id, "first": first},
        )
        nodes = data.get("team", {}).get("issues", {}).get("nodes", [])
        return [self._parse_issue(node) for node in nodes]

    async def get_team(self, team_id: str) -> Team | None:
        """Fetch a single team by ID.

        Args:
            team_id: The Linear team ID.

        Returns:
            A Team instance, or None if not found.
        """
        query = """
        query GetTeam($id: String!) {
            team(id: $id) {
                id
                name
                key
            }
        }
        """
        data = await self._client.execute_async(query, {"id": team_id})
        team_data = data.get("team")
        if not team_data:
            return None
        return Team(
            id=team_data["id"],  # type: ignore[call-arg]
            name=team_data["name"],  # type: ignore[call-arg]
            key=team_data["key"],  # type: ignore[call-arg]
        )

    async def search_issues(self, term: str) -> list[Issue]:
        """Search for issues by keyword term.

        Args:
            term: The search term.

        Returns:
            A list of matching Issue instances.
        """
        query = """
        query SearchIssues($term: String!) {
            searchIssues(term: $term) {
                nodes {
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
        data = await self._client.execute_async(query, {"term": term})
        nodes = data.get("searchIssues", {}).get("nodes", [])
        return [self._parse_issue(node) for node in nodes]

    async def get_user(self, user_id: str) -> User | None:
        """Fetch a single user by ID.

        Args:
            user_id: The Linear user ID.

        Returns:
            A User instance, or None if not found.
        """
        query = """
        query GetUser($id: String!) {
            user(id: $id) {
                id
                name
                email
                active
            }
        }
        """
        data = await self._client.execute_async(query, {"id": user_id})
        user_data = data.get("user")
        if not user_data:
            return None
        return self._parse_user(user_data)

    def _parse_user(self, data: dict[str, Any]) -> User:
        """Parse user data from API response.

        Args:
            data: User data dictionary from API.

        Returns:
            User instance.
        """
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
            status=data.get("status", {}).get("name")  # type: ignore[call-arg]
            if data.get("status")
            else None,
            assignee=self._parse_user(data["assignee"])  # type: ignore[call-arg]
            if data.get("assignee")
            else None,
        )
