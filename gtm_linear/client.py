"""HTTP transport for the Linear GraphQL API."""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

import httpx
from pydantic import SecretStr, ValidationError

from .exceptions import (
    GraphQLError,
    LinearGraphQLError,
    LinearHTTPError,
    LinearResponseError,
)

if TYPE_CHECKING:
    from .settings import LinearSettings

HTTP_OK = 200
DEFAULT_TIMEOUT = 30.0


def _coerce_error(entry: Any) -> GraphQLError:  # noqa: ANN401
    """Coerce one ``errors[]`` entry into a :class:`GraphQLError`.

    Well-formed dicts validate directly; non-dict values and dicts with an
    unusable ``message`` degrade to ``GraphQLError(message=str(entry))``; dicts
    that fail validation for an optional field are salvaged by
    :func:`_salvage_error`. Every branch returns a :class:`GraphQLError`, so
    ``LinearClient._handle_response`` only ever raises :class:`LinearAPIError`
    subclasses. Without this guard, ``GraphQLError.model_validate`` raises
    ``pydantic.ValidationError`` for spec-violating entries, which escapes
    ``execute`` / ``execute_async`` and bypasses the documented
    ``except LinearAPIError`` contract.
    """
    if not isinstance(entry, dict):
        return GraphQLError(message=str(entry))
    try:
        return GraphQLError.model_validate(entry)
    except ValidationError:
        return _salvage_error(entry)


def _salvage_error(entry: dict[str, Any]) -> GraphQLError:
    """Salvage a spec-violating ``errors[]`` dict entry.

    Keeps everything usable and drops only what actually fails: a string
    ``message``, a dict ``extensions``, ``path`` / ``locations`` lists that
    validate — each dropped independently, so a valid ``locations`` survives
    an invalid ``path`` — and every vendor-specific extra key (``GraphQLError``
    uses ``extra="allow"`` so Linear's non-spec keys survive salvage exactly
    as they survive direct validation). An unusable ``message`` degrades to
    ``GraphQLError(message=str(entry))`` — the same fallback non-dict entries
    always use.
    """
    message = entry.get("message")
    if not isinstance(message, str):
        return GraphQLError(message=str(entry))
    known_fields = {"message", "path", "locations", "extensions"}
    cleaned: dict[str, Any] = {
        key: value for key, value in entry.items() if key not in known_fields
    }
    cleaned["message"] = message
    if isinstance(entry.get("extensions"), dict):
        cleaned["extensions"] = entry["extensions"]
    for field in ("path", "locations"):
        if isinstance(entry.get(field), list):
            cleaned[field] = entry[field]
    while True:
        try:
            return GraphQLError.model_validate(cleaned)
        except ValidationError as exc:
            # Drop the optional fields pydantic actually rejected — a valid
            # sibling survives. Any other failure cannot be fixed by dropping
            # an optional field, so the entry degrades wholesale.
            failing = {e["loc"][0] for e in exc.errors() if e["loc"]}
            droppable = [f for f in ("path", "locations") if f in failing]
            if not droppable:
                return GraphQLError(message=str(entry))
            for field in droppable:
                cleaned.pop(field, None)


class LinearClient:
    """GraphQL client for the Linear API.

    The sync and async paths share :meth:`_handle_response`; they used to carry
    byte-identical copies of the response handling, and only the sync copy was
    covered by tests.
    """

    BASE_URL = "https://api.linear.app/graphql"
    HTTP_OK = HTTP_OK

    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        base_url: str | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize LinearClient.

        Args:
            api_key: Linear API key (``lin_api_...``). Held as a
                :class:`~pydantic.SecretStr` so it is not printed by ``repr`` or
                exposed in tracebacks.
            base_url: Override the API endpoint.
            timeout: Request timeout in seconds.
        """
        self._api_key = (
            api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        )
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": self._api_key.get_secret_value(),
        }
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(cls, settings: LinearSettings) -> Self:
        """Build a client from a :class:`~gtm_linear.settings.LinearSettings`."""
        return cls(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )

    @classmethod
    def from_env(cls) -> Self:
        """Build a client from ``LINEAR_*`` environment variables or ``.env.local``."""
        from .settings import LinearSettings

        return cls.from_settings(LinearSettings())

    def __repr__(self) -> str:
        """Render without the API key."""
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            f"api_key=SecretStr('**********'))"
        )

    @property
    def api_key(self) -> SecretStr:
        """The API key, wrapped so it is not accidentally logged."""
        return self._api_key

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=self._headers, timeout=self.timeout)
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self.timeout,
            )
        return self._async_client

    def _payload(
        self,
        query: str,
        variables: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        return payload

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Turn an HTTP response into the GraphQL ``data`` object, or raise.

        Args:
            response: The raw HTTP response.

        Returns:
            The contents of the response's ``data`` key.

        Raises:
            LinearGraphQLError: The response carried a GraphQL ``errors`` payload.
            LinearHTTPError: The response had a non-200 status.
            LinearResponseError: The body was not a usable GraphQL envelope.
        """
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and body.get("errors"):
            raw_errors = body["errors"]
            # A truthy non-list ``errors`` value (a scalar, a bare string, a
            # single error object) is normalized to one entry instead of
            # raising ``TypeError``, splitting a string into per-character
            # errors, or iterating a dict's keys.
            entries = raw_errors if isinstance(raw_errors, list) else [raw_errors]
            errors = [_coerce_error(e) for e in entries]
            summary = "; ".join(e.message for e in errors)
            raise LinearGraphQLError(f"GraphQL error: {summary}", errors=errors)

        if response.status_code != HTTP_OK:
            raise LinearHTTPError(
                f"HTTP error: {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        if not isinstance(body, dict):
            error_msg = "Invalid response format: expected a JSON object"
            raise LinearResponseError(error_msg)

        if "data" not in body:
            # Previously escaped as a bare KeyError, bypassing the error contract.
            error_msg = "Invalid response format: no 'data' key"
            raise LinearResponseError(error_msg)

        return body["data"]

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL document synchronously.

        Args:
            query: GraphQL document.
            variables: Optional variables.

        Returns:
            The GraphQL response's ``data`` object.

        Raises:
            LinearAPIError: If the request fails. See :meth:`_handle_response`.
        """
        response = self._get_client().post(
            self.base_url,
            json=self._payload(query, variables),
        )
        return self._handle_response(response)

    async def execute_async(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL document asynchronously.

        Args:
            query: GraphQL document.
            variables: Optional variables.

        Returns:
            The GraphQL response's ``data`` object.

        Raises:
            LinearAPIError: If the request fails. See :meth:`_handle_response`.
        """
        response = await self._get_async_client().post(
            self.base_url,
            json=self._payload(query, variables),
        )
        return self._handle_response(response)

    def close(self) -> None:
        """Close the synchronous connection.

        Raises:
            RuntimeError: If an async client is still open; use :meth:`aclose`.
        """
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            error_msg = "Async client is still open; call await aclose() first"
            raise RuntimeError(error_msg)

    async def aclose(self) -> None:
        """Close the asynchronous connection."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def __enter__(self) -> Self:
        """Enter the sync context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the sync connection on exit."""
        self.close()

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close both connections on exit."""
        await self.aclose()
        self.close()
