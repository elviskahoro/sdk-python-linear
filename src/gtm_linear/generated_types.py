import strawberry
from typing import Any


@strawberry.type
class User:
    id: str
    name: str
    email: str
    active: bool


@strawberry.type
class Team:
    id: str
    name: str
    key: str


@strawberry.type
class Issue:
    id: str
    title: str
    description: str | None
    identifier: str
    url: str
    priority: int | None
    status: str | None
    assignee: User | None


@strawberry.type
class Project:
    id: str
    name: str
    slug: str


@strawberry.type
class PageInfo:
    hasNextPage: bool
    hasPreviousPage: bool
    startCursor: str | None
    endCursor: str | None


@strawberry.type
class IssueConnection:
    nodes: list[Issue]
    pageInfo: PageInfo


@strawberry.type
class UserConnection:
    nodes: list[User]
    pageInfo: PageInfo


@strawberry.type
class TeamConnection:
    nodes: list[Team]
    pageInfo: PageInfo


@strawberry.type
class ProjectConnection:
    nodes: list[Project]
    pageInfo: PageInfo


@strawberry.input
class IssueCreateInput:
    title: str
    teamId: str


@strawberry.input
class IssueUpdateInput:
    title: str | None = None
    description: str | None = None