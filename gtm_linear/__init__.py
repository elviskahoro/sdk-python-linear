"""Async-first Python SDK for the Linear GraphQL API.

Types are generated from Linear's published schema (see scripts/codegen.py), so what
the SDK says a field contains is what the schema says it contains. They are plain
Pydantic models: attribute access is fully typed, and responses are validated.

The generated classes are named after the GraphQL fragments that produce them
(``IssueFields``); the aliases below are the intended public spelling.
"""

from importlib.metadata import PackageNotFoundError, version

from ._generated.CreateComment import CommentCreateInput
from ._generated.CreateIssue import IssueCreateInput
from ._generated.ListIssues import (
    ListIssuesResultIssues as IssueConnection,
    PaginationOrderBy,
)
from ._generated.SearchIssues import (
    SearchIssuesResultSearchIssues as IssueSearchConnection,
)
from ._generated.UpdateIssue import IssueUpdateInput
from ._generated.fragments import (
    CommentFields as Comment,
    IssueFields as Issue,
    IssueSearchResultFields as IssueSearchResult,
    PageInfoFields as PageInfo,
    TeamFields as Team,
    UserFields as User,
)
from .client import LinearClient
from .exceptions import LinearAPIError
from .models import LinearModel
from .mutations import LinearMutations
from .queries import LinearQueries

try:
    __version__ = version("gtm-linear")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = [
    "Comment",
    "CommentCreateInput",
    "Issue",
    "IssueConnection",
    "IssueCreateInput",
    "IssueSearchConnection",
    "IssueSearchResult",
    "IssueUpdateInput",
    "LinearAPIError",
    "LinearClient",
    "LinearModel",
    "LinearMutations",
    "LinearQueries",
    "PageInfo",
    "PaginationOrderBy",
    "Team",
    "User",
]
