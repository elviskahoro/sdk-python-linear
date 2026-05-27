import strawberry


@strawberry.type  # type: ignore[misc]
class User:
    """Linear user type."""

    id: str
    name: str
    email: str
    active: bool


@strawberry.type  # type: ignore[misc]
class Team:
    """Linear team type."""

    id: str
    name: str
    key: str


@strawberry.type  # type: ignore[misc]
class Issue:
    """Linear issue type."""

    id: str
    title: str
    description: str | None
    identifier: str
    url: str
    priority: int | None
    status: str | None
    assignee: User | None


@strawberry.type  # type: ignore[misc]
class Project:
    """Linear project type."""

    id: str
    name: str
    slug: str


@strawberry.type  # type: ignore[misc]
class PageInfo:
    """Pagination info type."""

    hasNextPage: bool
    hasPreviousPage: bool
    startCursor: str | None
    endCursor: str | None


@strawberry.type  # type: ignore[misc]
class IssueConnection:
    """Connection of issues with pagination."""

    nodes: list[Issue]
    pageInfo: PageInfo


@strawberry.type  # type: ignore[misc]
class UserConnection:
    """Connection of users with pagination."""

    nodes: list[User]
    pageInfo: PageInfo


@strawberry.type  # type: ignore[misc]
class TeamConnection:
    """Connection of teams with pagination."""

    nodes: list[Team]
    pageInfo: PageInfo


@strawberry.type  # type: ignore[misc]
class ProjectConnection:
    """Connection of projects with pagination."""

    nodes: list[Project]
    pageInfo: PageInfo


@strawberry.input  # type: ignore[misc]
class IssueCreateInput:
    """Input type for creating an issue."""

    title: str
    teamId: str
    description: str | None = None


@strawberry.input  # type: ignore[misc]
class IssueUpdateInput:
    """Input type for updating an issue."""

    title: str | None = None
    description: str | None = None
