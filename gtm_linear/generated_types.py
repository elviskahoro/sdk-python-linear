"""Strawberry public types backed by Pydantic validation models."""

from enum import StrEnum

import strawberry
from strawberry.experimental import pydantic

from .models import (
    CommentModel,
    IssueCreateInputModel,
    IssueConnectionModel,
    IssueModel,
    IssueUpdateInputModel,
    PageInfoModel,
    ProjectModel,
    ProjectConnectionModel,
    TeamModel,
    TeamConnectionModel,
    UserModel,
    UserConnectionModel,
)


@pydantic.type(model=UserModel, all_fields=True)
class User:
    """Linear user type."""


@pydantic.type(model=TeamModel, all_fields=True)
class Team:
    """Linear team type."""


@pydantic.type(model=IssueModel, all_fields=True)
class Issue:
    """Linear issue type."""


@pydantic.type(model=CommentModel, all_fields=True)
class Comment:
    """Linear issue comment type."""


@pydantic.type(model=ProjectModel, all_fields=True)
class Project:
    """Linear project type."""


@pydantic.type(model=PageInfoModel, all_fields=True)
class PageInfo:
    """Pagination info type."""


@pydantic.input(model=IssueCreateInputModel, all_fields=True)
class IssueCreateInput:
    """Input type for creating an issue."""


@pydantic.input(model=IssueUpdateInputModel, all_fields=True)
class IssueUpdateInput:
    """Input type for updating an issue."""


@strawberry.enum  # type: ignore[misc]
class WorkflowStateType(StrEnum):
    """Linear workflow state categories usable in issue filters."""

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


@strawberry.enum  # type: ignore[misc]
class PaginationOrderBy(StrEnum):
    """Supported Linear ordering for paginated issue queries."""

    CREATED_AT = "createdAt"
    UPDATED_AT = "updatedAt"


@strawberry.input  # type: ignore[misc]
class StringComparatorInput:
    """Linear string comparators used by the supported nested filters."""

    eq: str | None = None
    neq: str | None = None
    in_: list[str] | None = strawberry.field(default=None, name="in")
    nin: list[str] | None = None


@strawberry.input  # type: ignore[misc]
class WorkflowStateTypeComparatorInput:
    """Linear workflow-state-type comparators."""

    eq: WorkflowStateType | None = None
    neq: WorkflowStateType | None = None
    in_: list[WorkflowStateType] | None = strawberry.field(default=None, name="in")
    nin: list[WorkflowStateType] | None = None


@strawberry.input  # type: ignore[misc]
class TeamFilterInput:
    """Nested Linear team filter used by issue filtering."""

    id: StringComparatorInput | None = None


@strawberry.input  # type: ignore[misc]
class WorkflowStateFilterInput:
    """Nested Linear workflow-state filter used by issue filtering."""

    type: WorkflowStateTypeComparatorInput | None = None


@strawberry.input  # type: ignore[misc]
class IssueFilterInput:
    """Linear-shaped issue filter accepted by :meth:`LinearQueries.list_issues_page`."""

    team: TeamFilterInput | None = None
    state: WorkflowStateFilterInput | None = None


@pydantic.type(model=IssueConnectionModel, all_fields=True)
class IssueConnection:
    """Connection of issues with pagination."""


@pydantic.type(model=UserConnectionModel, all_fields=True)
class UserConnection:
    """Connection of users with pagination."""


@pydantic.type(model=TeamConnectionModel, all_fields=True)
class TeamConnection:
    """Connection of teams with pagination."""


@pydantic.type(model=ProjectConnectionModel, all_fields=True)
class ProjectConnection:
    """Connection of projects with pagination."""
