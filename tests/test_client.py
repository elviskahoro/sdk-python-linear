import pytest
import httpx
import respx

from gtm_linear import LinearClient, LinearAPIError


@pytest.fixture
def api_key():
    return "lin_api_test_key_12345"


@pytest.fixture
def client(api_key):
    return LinearClient(api_key)


class TestLinearClient:
    def test_init_sets_api_key(self, client, api_key):
        assert client.api_key == api_key

    def test_init_sets_correct_headers(self, client, api_key):
        assert client._headers["Content-Type"] == "application/json"
        assert client._headers["Authorization"] == api_key
        assert "Bearer" not in client._headers["Authorization"]

    def test_context_manager(self, api_key):
        with LinearClient(api_key) as c:
            assert c.api_key == api_key
        assert c._client is None
        assert c._async_client is None


class TestLinearClientExecute:
    @respx.mock
    def test_execute_successful_query(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issue": {
                            "id": "issue-1",
                            "title": "Test Issue",
                        }
                    }
                },
            )
        )

        result = client.execute("query { issue(id: 1) { id title } }")

        assert result["issue"]["id"] == "issue-1"
        assert result["issue"]["title"] == "Test Issue"

    @respx.mock
    def test_execute_with_variables(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "id": "user-1",
                            "name": "Test User",
                        }
                    }
                },
            )
        )

        result = client.execute(
            "query GetUser($id: String!) { user(id: $id) { id name } }",
            {"id": "user-123"},
        )

        assert result["user"]["id"] == "user-1"

    @respx.mock
    def test_execute_graphql_error_raises_exception(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [{"message": "Invalid query"}],
                },
            )
        )

        with pytest.raises(LinearAPIError) as exc_info:
            client.execute("query { invalid }")

        assert "Invalid query" in str(exc_info.value)

    @respx.mock
    def test_execute_http_error_raises_exception(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(500, text="Internal Server Error"),
        )

        with pytest.raises(LinearAPIError) as exc_info:
            client.execute("query { issue }")

        assert "HTTP error" in str(exc_info.value)
        assert "500" in str(exc_info.value)

    @respx.mock
    def test_execute_unauthorized_raises_exception(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(401, text="Unauthorized"),
        )

        with pytest.raises(LinearAPIError):
            client.execute("query { issue }")


class TestLinearClientAsyncExecute:
    @pytest.mark.asyncio
    @respx.mock
    async def test_execute_async_successful_query(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "team": {"id": "team-1", "name": "Engineering"}
                    }
                },
            )
        )

        result = await client.execute_async("query { team(id: 1) { id name } }")

        assert result["team"]["name"] == "Engineering"

    @pytest.mark.asyncio
    @respx.mock
    async def test_execute_async_graphql_error(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [{"message": "Authentication required"}],
                },
            )
        )

        with pytest.raises(LinearAPIError) as exc_info:
            await client.execute_async("query { issue }")

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_execute_async_http_error(self, client):
        respx.post(client.BASE_URL).mock(
            return_value=httpx.Response(403, text="Forbidden"),
        )

        with pytest.raises(LinearAPIError):
            await client.execute_async("query { issue }")

    @pytest.mark.asyncio
    async def test_async_context_manager(self, api_key):
        async with LinearClient(api_key) as c:
            assert c.api_key == api_key
        assert c._client is None
        assert c._async_client is None