from types import TracebackType
from typing import Any, Self

import httpx

from .exceptions import LinearAPIError


class LinearClient:
    """GraphQL client for Linear API."""

    BASE_URL = "https://api.linear.app/graphql"
    HTTP_OK = 200

    def __init__(self, api_key: str) -> None:
        """Initialize LinearClient.

        Args:
            api_key: Linear API key for authentication.
        """
        self.api_key = api_key
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": api_key,
        }
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=self._headers)
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(headers=self._headers)
        return self._async_client

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a synchronous GraphQL query.

        Args:
            query: GraphQL query string.
            variables: Optional variables for the query.

        Returns:
            The data from the GraphQL response.

        Raises:
            LinearAPIError: If the API request fails.
        """
        client = self._get_client()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        response = client.post(self.BASE_URL, json=payload)

        try:
            data = response.json()
        except ValueError:
            data = None

        if isinstance(data, dict) and "errors" in data:
            error_messages = [e.get("message", str(e)) for e in data["errors"]]
            error_msg = f"GraphQL error: {'; '.join(error_messages)}"
            raise LinearAPIError(error_msg, errors=data["errors"])

        if response.status_code != self.HTTP_OK:
            error_msg = f"HTTP error: {response.status_code}"
            raise LinearAPIError(
                error_msg,
                errors=[
                    {"status_code": response.status_code, "message": response.text},
                ],
            )

        if not isinstance(data, dict):
            error_msg = "Invalid response format"
            raise LinearAPIError(error_msg, errors=[])

        return data["data"]

    async def execute_async(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an asynchronous GraphQL query.

        Args:
            query: GraphQL query string.
            variables: Optional variables for the query.

        Returns:
            The data from the GraphQL response.

        Raises:
            LinearAPIError: If the API request fails.
        """
        client = self._get_async_client()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        response = await client.post(self.BASE_URL, json=payload)

        try:
            data = response.json()
        except ValueError:
            data = None

        if isinstance(data, dict) and "errors" in data:
            error_messages = [e.get("message", str(e)) for e in data["errors"]]
            error_msg = f"GraphQL error: {'; '.join(error_messages)}"
            raise LinearAPIError(error_msg, errors=data["errors"])

        if response.status_code != self.HTTP_OK:
            error_msg = f"HTTP error: {response.status_code}"
            raise LinearAPIError(
                error_msg,
                errors=[
                    {"status_code": response.status_code, "message": response.text},
                ],
            )

        if not isinstance(data, dict):
            error_msg = "Invalid response format"
            raise LinearAPIError(error_msg, errors=[])

        return data["data"]

    def close(self) -> None:
        """Close the synchronous client connection.

        Raises:
            RuntimeError: If an asynchronous client is still open. Use
                :meth:`aclose` to close it from an async context.
        """
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            error_msg = "Async client is still open; call await aclose() first"
            raise RuntimeError(error_msg)

    async def aclose(self) -> None:
        """Close the asynchronous client connection."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def __enter__(self) -> Self:
        """Enter context manager.

        Returns:
            Self for context manager use.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager.

        Args:
            exc_type: Exception type if raised.
            exc_val: Exception value if raised.
            exc_tb: Exception traceback if raised.
        """
        self.close()

    async def __aenter__(self) -> Self:
        """Enter async context manager.

        Returns:
            Self for context manager use.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager.

        Args:
            exc_type: Exception type if raised.
            exc_val: Exception value if raised.
            exc_tb: Exception traceback if raised.
        """
        await self.aclose()
        self.close()
