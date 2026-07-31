from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from gtm_linear import LinearAPIError, LinearClient

API_URL = LinearClient.BASE_URL


@respx.mock  # type: ignore[misc]
def test_execute_sync_returns_data() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
    )
    with LinearClient(api_key="key") as client:
        data = client.execute("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}  # noqa: S101


@respx.mock  # type: ignore[misc]
def test_execute_passes_variables() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"ok": True}}),
    )
    with LinearClient(api_key="key") as client:
        client.execute("query($x: String!){ ok }", {"x": "y"})
    assert route.calls.last.request.read() == (  # noqa: S101
        b'{"query":"query($x: String!){ ok }","variables":{"x":"y"}}'
    )


@respx.mock  # type: ignore[misc]
def test_execute_raises_on_http_error() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(500, text="boom"))
    with LinearClient(api_key="key") as client, pytest.raises(LinearAPIError):
        client.execute("query { viewer { id } }")


@respx.mock  # type: ignore[misc]
def test_execute_raises_on_graphql_errors() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={"errors": [{"message": "bad query"}]},
        ),
    )
    with LinearClient(api_key="key") as client, pytest.raises(LinearAPIError) as exc:
        client.execute("query { viewer { id } }")
    assert "bad query" in str(exc.value)  # noqa: S101


@respx.mock  # type: ignore[misc]
def test_authorization_header_is_set() -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {}}),
    )
    with LinearClient(api_key="secret-key") as client:
        client.execute("query { __typename }")
    assert route.calls.last.request.headers["Authorization"] == "secret-key"  # noqa: S101


@respx.mock  # type: ignore[misc]
async def test_execute_async_returns_data() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
    )
    async with LinearClient(api_key="key") as client:
        data = await client.execute_async("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}  # noqa: S101


async def test_aclose_closes_async_client_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    async_client = AsyncMock(spec=httpx.AsyncClient)
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client_factory = Mock(return_value=async_client)
    monkeypatch.setattr("gtm_linear.client.httpx.AsyncClient", async_client_factory)

    await client.execute_async("query { __typename }")

    await client.aclose()
    await client.aclose()

    async_client_factory.assert_called_once()
    async_client.aclose.assert_awaited_once()


async def test_close_raises_when_async_client_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    sync_client = Mock(spec=httpx.Client)
    async_client = AsyncMock(spec=httpx.AsyncClient)
    sync_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    monkeypatch.setattr(
        "gtm_linear.client.httpx.Client",
        Mock(return_value=sync_client),
    )
    monkeypatch.setattr(
        "gtm_linear.client.httpx.AsyncClient",
        Mock(return_value=async_client),
    )

    client.execute("query { __typename }")
    await client.execute_async("query { __typename }")

    with pytest.raises(RuntimeError, match="call await aclose\\(\\) first"):
        client.close()

    sync_client.close.assert_called_once()
    async_client.aclose.assert_not_awaited()
    await client.aclose()
    async_client.aclose.assert_awaited_once()


async def test_async_context_manager_closes_both_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    sync_client = Mock(spec=httpx.Client)
    async_client = AsyncMock(spec=httpx.AsyncClient)
    sync_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    monkeypatch.setattr(
        "gtm_linear.client.httpx.Client",
        Mock(return_value=sync_client),
    )
    monkeypatch.setattr(
        "gtm_linear.client.httpx.AsyncClient",
        Mock(return_value=async_client),
    )

    async with client:
        client.execute("query { __typename }")
        await client.execute_async("query { __typename }")

    sync_client.close.assert_called_once()
    async_client.aclose.assert_awaited_once()
