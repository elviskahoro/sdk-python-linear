"""Typed errors for Linear API failures.

Linear returns errors in the standard GraphQL envelope, with a machine-readable code
under ``extensions.code``. That structure is modelled here so callers can branch on
``exc.errors[0].code == "RATELIMITED"`` instead of digging through raw dicts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GraphQLErrorLocation(BaseModel):
    """Position in the query document that produced an error."""

    line: int
    column: int


class GraphQLError(BaseModel):
    """A single entry from a GraphQL ``errors`` array.

    ``extra="allow"`` because Linear attaches vendor-specific keys that are useful
    to surface even though they are not part of the spec.
    """

    model_config = ConfigDict(extra="allow")

    message: str
    path: list[str | int] | None = None
    locations: list[GraphQLErrorLocation] | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def code(self) -> str | None:
        """Linear's machine-readable error code, e.g. ``AUTHENTICATION_ERROR``.

        Codes worth branching on: ``AUTHENTICATION_ERROR``, ``FORBIDDEN``,
        ``INVALID_INPUT``, ``RATELIMITED``.
        """
        code = self.extensions.get("code")
        return code if isinstance(code, str) else None


class LinearError(Exception):
    """Base class for every error this SDK raises."""


class LinearAPIError(LinearError):
    """A request to Linear failed.

    Attributes:
        message: Human-readable summary.
        errors: Structured GraphQL errors, empty for transport-level failures.
    """

    def __init__(
        self,
        message: str,
        errors: list[GraphQLError] | None = None,
    ) -> None:
        self.message = message
        self.errors = errors or []
        super().__init__(message)

    @property
    def codes(self) -> list[str]:
        """Every non-null ``extensions.code`` present, for convenient branching."""
        return [e.code for e in self.errors if e.code is not None]


class LinearGraphQLError(LinearAPIError):
    """Linear returned a GraphQL ``errors`` array."""


class LinearHTTPError(LinearAPIError):
    """Linear returned a non-200 status.

    ``status_code`` is a real attribute here. It used to be smuggled into a
    fabricated entry in ``errors``, which made that list two different shapes
    depending on how the request failed.
    """

    def __init__(self, message: str, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message)


class LinearResponseError(LinearAPIError):
    """The response was not a usable GraphQL envelope.

    Covers unparseable JSON, a non-object body, and a 200 response with no ``data``
    key — the last of which previously escaped as a bare ``KeyError``.
    """
