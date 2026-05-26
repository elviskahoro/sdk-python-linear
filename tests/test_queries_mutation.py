import pytest
import httpx
import respx

from gtm_linear import LinearClient, LinearQueries, LinearMutations
from gtm_linear.generated_types import IssueCreateInput, IssueUpdateInput


@pytest.fixture
def api_key():
    return "lin_api_test_key_12345"


@pytest.fixture
def client(api_key):
    return LinearClient(api_key)


@pytest.fixture
def queries(client):
    return LinearQueries(client)


@pytest.fixture
def mutations(client):
    return LinearMutations(client)


class TestLinearQueriesGetIssue:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue_success(self, queries):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issue": {
                            "id": "issue-123",
                            "title": "Fix bug",
                            "description": "Fix the bug in the code",
                            "identifier": "ENG-123",
                            "url": "https://linear.app/issue/ENG-123",
                            "priority": 2,
                            "status": {"name": "In Progress"},
                            "assignee": {
                                "id": "user-1",
                                "name": "John Doe",
                                "email": "john@example.com",
                                "active": True,
                            },
                        }
                    }
                },
            )
        )

        issue = await queries.get_issue("issue-123")

        assert issue is not None
        assert issue.id == "issue-123"
        assert issue.title == "Fix bug"
        assert issue.identifier == "ENG-123"
        assert issue.priority == 2
        assert issue.status == "In Progress"
        assert issue.assignee is not None
        assert issue.assignee.name == "John Doe"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue_not_found(self, queries):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"issue": None}},
            )
        )

        issue = await queries.get_issue("nonexistent")

        assert issue is None


class TestLinearQueriesListIssues:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_issues_success(self, queries):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "team": {
                            "issues": {
                                "nodes": [
                                    {
                                        "id": "issue-1",
                                        "title": "Issue 1",
                                        "identifier": "ENG-1",
                                        "url": "https://linear.app/issue/ENG-1",
                                    },
                                    {
                                        "id": "issue-2",
                                        "title": "Issue 2",
                                        "identifier": "ENG-2",
                                        "url": "https://linear.app/issue/ENG-2",
                                    },
                                ]
                            }
                        }
                    }
                },
            )
        )

        issues = await queries.list_issues("team-123", first=10)

        assert len(issues) == 2
        assert issues[0].id == "issue-1"
        assert issues[1].id == "issue-2"


class TestLinearQueriesGetTeam:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_team_success(self, queries):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "team": {
                            "id": "team-123",
                            "name": "Engineering",
                            "key": "ENG",
                        }
                    }
                },
            )
        )

        team = await queries.get_team("team-123")

        assert team is not None
        assert team.id == "team-123"
        assert team.name == "Engineering"
        assert team.key == "ENG"


class TestLinearQueriesSearchIssues:
    @pytest.mark.asyncio
    @respx.mock
    async def test_search_issues_success(self, queries):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "searchIssues": {
                            "nodes": [
                                {
                                    "id": "issue-1",
                                    "title": "Bug fix",
                                    "identifier": "ENG-1",
                                    "url": "https://linear.app/issue/ENG-1",
                                }
                            ]
                        }
                    }
                },
            )
        )

        issues = await queries.search_issues("bug")

        assert len(issues) == 1
        assert issues[0].title == "Bug fix"


class TestLinearMutationsCreateIssue:
    @pytest.mark.asyncio
    @respx.mock
    async def test_create_issue_success(self, mutations):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-new",
                                "title": "New Issue",
                                "identifier": "ENG-999",
                                "url": "https://linear.app/issue/ENG-999",
                            },
                        }
                    }
                },
            )
        )

        issue_input = IssueCreateInput(title="New Issue", teamId="team-123")
        issue = await mutations.create_issue(issue_input)

        assert issue is not None
        assert issue.id == "issue-new"
        assert issue.title == "New Issue"


class TestLinearMutationsUpdateIssue:
    @pytest.mark.asyncio
    @respx.mock
    async def test_update_issue_success(self, mutations):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issueUpdate": {
                            "success": True,
                            "issue": {
                                "id": "issue-123",
                                "title": "Updated Title",
                                "identifier": "ENG-123",
                                "url": "https://linear.app/issue/ENG-123",
                            },
                        }
                    }
                },
            )
        )

        update = IssueUpdateInput(title="Updated Title")
        issue = await mutations.update_issue("issue-123", update)

        assert issue is not None
        assert issue.title == "Updated Title"


class TestLinearMutationsDeleteIssue:
    @pytest.mark.asyncio
    @respx.mock
    async def test_delete_issue_success(self, mutations):
        respx.post(LinearClient.BASE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issueDelete": {
                            "success": True,
                            "lastSyncId": 123,
                        }
                    }
                },
            )
        )

        result = await mutations.delete_issue("issue-123")

        assert result is True