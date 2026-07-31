from enum import Enum
from typing import Any, TypeAlias, cast

import strawberry

from .client import LinearClient
from .generated_types import (
    Issue,
    IssueConnection,
    IssueFilterInput,
    PageInfo,
    PaginationOrderBy,
    StringComparatorInput,
    Team,
    TeamFilterInput,
    User,
)
from .models import (
    IssueConnectionModel,
    IssueModel,
    PageInfoModel,
    TeamModel,
    UserModel,
)

GraphQLInputValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | Enum
    | list["GraphQLInputValue"]
    | dict[str, "GraphQLInputValue"]
)
GraphQLVariableValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["GraphQLVariableValue"]
    | dict[str, "GraphQLVariableValue"]
)


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
        issue_filter = IssueFilterInput(  # type: ignore[call-arg]
            team=TeamFilterInput(  # type: ignore[call-arg]
                id=StringComparatorInput(eq=team_id),  # type: ignore[call-arg]
            ),
        )
        connection = await self.list_issues_page(issue_filter, first=first)
        return connection.nodes  # type: ignore[missing-attribute]

    async def list_issues_page(
        self,
        filter: IssueFilterInput | None = None,  # noqa: A002
        first: int = 50,
        after: str | None = None,
        order_by: PaginationOrderBy | None = None,
        *,
        include_archived: bool = False,
    ) -> IssueConnection:
        """List a filtered page of issues with cursor metadata."""
        query = """
        query ListIssues(
            $filter: IssueFilter,
            $first: Int!,
            $after: String,
            $orderBy: PaginationOrderBy,
            $includeArchived: Boolean!
        ) {
            issues(filter: $filter, first: $first, after: $after, orderBy: $orderBy, includeArchived: $includeArchived) {
                nodes {
                    id title description identifier url priority
                    status: state { name }
                    assignee { id name email active }
                }
                pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
            }
        }
        """
        data = await self._client.execute_async(
            query,
            {
                "filter": self._serialize_graphql_input(filter),
                "first": first,
                "after": after,
                "orderBy": order_by.value if order_by else None,
                "includeArchived": include_archived,
            },
        )
        connection_data = data.get("issues", {})
        parsed_nodes = [
            self._parse_issue(node) for node in connection_data.get("nodes", [])
        ]
        page_info = self._parse_page_info(connection_data.get("pageInfo", {}))
        return IssueConnection.from_pydantic(  # type: ignore[missing-attribute]
            IssueConnectionModel.model_validate(
                {
                    "nodes": [  # type: ignore[missing-attribute]
                        node.to_pydantic() for node in parsed_nodes
                    ],
                    "pageInfo": page_info.to_pydantic(),  # type: ignore[missing-attribute]
                },
            ),
        )

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
        return Team.from_pydantic(TeamModel.model_validate(team_data))

    async def get_team_by_key(self, key: str) -> Team | None:
        """Fetch a single team by its human-readable key."""
        query = """
        query GetTeamByKey($key: String!) {
            teams(filter: { key: { eq: $key } }, first: 1) {
                nodes { id name key }
            }
        }
        """
        data = await self._client.execute_async(query, {"key": key})
        nodes = data.get("teams", {}).get("nodes", [])
        if not nodes:
            return None
        return Team.from_pydantic(TeamModel.model_validate(nodes[0]))

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
        return User.from_pydantic(UserModel.model_validate(data))

    def _parse_page_info(self, data: dict[str, Any]) -> PageInfo:
        """Parse pagination metadata from a Linear connection response."""
        return PageInfo.from_pydantic(PageInfoModel.model_validate(data))

    def _serialize_graphql_input(
        self,
        value: IssueFilterInput | None,
    ) -> dict[str, Any] | None:
        """Serialize a Strawberry input into a sparse Linear GraphQL variable."""
        if value is None:
            return None
        raw_value = cast(
            "dict[str, GraphQLInputValue]",
            strawberry.asdict(value),
        )
        serialized = self._normalize_graphql_value(raw_value)
        return serialized if isinstance(serialized, dict) else None

    def _normalize_graphql_value(
        self,
        value: GraphQLInputValue,
    ) -> GraphQLVariableValue:
        """Omit null fields, preserve GraphQL names, and unwrap enum values."""
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list):
            return [self._normalize_graphql_value(item) for item in value]
        if isinstance(value, dict):
            return {
                "in" if key == "in_" else key: self._normalize_graphql_value(item)
                for key, item in value.items()
                if item is not None
            }
        return value

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
        return Issue.from_pydantic(IssueModel.model_validate(normalized_data))
