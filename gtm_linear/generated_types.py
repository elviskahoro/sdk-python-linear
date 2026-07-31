"""Strawberry public types backed by Pydantic validation models."""

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
