import httpx
from typing import Any

from .exceptions import LinearAPIError


class LinearClient:
    BASE_URL = "https://api.linear.app/graphql"

    def __init__(self, api_key: str):
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

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._get_client()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        response = client.post(self.BASE_URL, json=payload)

        if response.status_code != 200:
            raise LinearAPIError(
                f"HTTP error: {response.status_code}",
                errors=[{"status_code": response.status_code, "message": response.text}],
            )

        data = response.json()

        if "errors" in data:
            error_messages = [e.get("message", str(e)) for e in data["errors"]]
            raise LinearAPIError(
                f"GraphQL error: {'; '.join(error_messages)}",
                errors=data["errors"],
            )

        return data["data"]

    async def execute_async(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._get_async_client()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        response = await client.post(self.BASE_URL, json=payload)

        if response.status_code != 200:
            raise LinearAPIError(
                f"HTTP error: {response.status_code}",
                errors=[{"status_code": response.status_code, "message": response.text}],
            )

        data = response.json()

        if "errors" in data:
            error_messages = [e.get("message", str(e)) for e in data["errors"]]
            raise LinearAPIError(
                f"GraphQL error: {'; '.join(error_messages)}",
                errors=data["errors"],
            )

        return data["data"]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            self._async_client.close()
            self._async_client = None

    def __enter__(self) -> "LinearClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "LinearClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()