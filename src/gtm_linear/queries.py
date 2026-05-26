from .client import LinearClient
from .generated_types import Issue, Team, Project, User, IssueConnection, IssueCreateInput, IssueUpdateInput


class LinearQueries:
    def __init__(self, client: LinearClient):
        self._client = client

    async def get_issue(self, issue_id: str) -> Issue | None:
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
        return Issue(
            id=issue_data["id"],
            title=issue_data["title"],
            description=issue_data.get("description"),
            identifier=issue_data["identifier"],
            url=issue_data["url"],
            priority=issue_data.get("priority"),
            status=issue_data.get("status", {}).get("name"),
            assignee=self._parse_user(issue_data.get("assignee")),
        )

    async def list_issues(self, team_id: str, first: int = 50) -> list[Issue]:
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
        data = await self._client.execute_async(query, {"teamId": team_id, "first": first})
        nodes = data.get("team", {}).get("issues", {}).get("nodes", [])
        return [self._parse_issue(node) for node in nodes]

    async def get_team(self, team_id: str) -> Team | None:
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
            id=team_data["id"],
            name=team_data["name"],
            key=team_data["key"],
        )

    async def search_issues(self, term: str) -> list[Issue]:
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
        return User(
            id=user_data["id"],
            name=user_data["name"],
            email=user_data.get("email", ""),
            active=user_data.get("active", False),
        )

    def _parse_user(self, data: dict | None) -> User | None:
        if not data:
            return None
        return User(
            id=data["id"],
            name=data["name"],
            email=data.get("email", ""),
            active=data.get("active", False),
        )

    def _parse_issue(self, data: dict) -> Issue:
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