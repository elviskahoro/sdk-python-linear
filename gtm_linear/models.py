"""Pydantic models used to validate Linear API data."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class LinearModel(BaseModel):
    """Base model for API payloads, ignoring fields outside the SDK projection."""

    model_config = ConfigDict(extra="ignore")


class UserModel(LinearModel):
    id: str
    name: str
    email: str = ""
    active: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def normalize_missing_email(cls, value: object) -> object:
        return "" if value is None else value


class TeamModel(LinearModel):
    id: str
    name: str
    key: str


class IssueModel(LinearModel):
    id: str
    title: str
    description: str | None = None
    identifier: str
    url: str
    priority: int | None = None
    status: str | None = None
    assignee: UserModel | None = None


class CommentModel(LinearModel):
    id: str
    body: str
    url: str
    createdAt: datetime


class ProjectModel(LinearModel):
    id: str
    name: str
    slug: str


class PageInfoModel(LinearModel):
    hasNextPage: bool
    hasPreviousPage: bool
    startCursor: str | None = None
    endCursor: str | None = None


class IssueConnectionModel(LinearModel):
    nodes: list[IssueModel]
    pageInfo: PageInfoModel


class UserConnectionModel(LinearModel):
    nodes: list[UserModel]
    pageInfo: PageInfoModel


class TeamConnectionModel(LinearModel):
    nodes: list[TeamModel]
    pageInfo: PageInfoModel


class ProjectConnectionModel(LinearModel):
    nodes: list[ProjectModel]
    pageInfo: PageInfoModel


class IssueCreateInputModel(LinearModel):
    title: str
    teamId: str
    description: str | None = None
    labelIds: list[str] | None = None
    priority: int | None = None
    assigneeId: str | None = None
    projectId: str | None = None
    stateId: str | None = None


class IssueUpdateInputModel(LinearModel):
    title: str | None = None
    description: str | None = None
    labelIds: list[str] | None = None
    priority: int | None = None
    assigneeId: str | None = None
    projectId: str | None = None
    stateId: str | None = None


class CommentCreateInputModel(LinearModel):
    issueId: str
    body: str
