from .client import LinearClient
from .generated_types import Issue, IssueCreateInput, IssueUpdateInput, User


class LinearMutations:
    def __init__(self, client: LinearClient):
        self._client = client

    async def create_issue(self, input: IssueCreateInput) -> Issue:
        query = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    title
                    description
                    identifier
                    url
                    priority
                    status {
                        name
                    }
                    assignee {
                        id
                        name
                        email
                        active
                    }
                }
            }
        }
        """
        variables = {
            "input": {"title": input.title, "teamId": input.teamId},
        }
        if input.description:
            variables["input"]["description"] = input.description

        data = await self._client.execute_async(query, variables)
        issue_data = data.get("issueCreate", {}).get("issue")
        if not issue_data:
            raise ValueError("Failed to create issue")

        return self._parse_issue(issue_data)

    async def update_issue(self, issue_id: str, update: IssueUpdateInput) -> Issue:
        query = """
        mutation UpdateIssue($id: String!, $update: IssueUpdateInput!) {
            issueUpdate(id: $id, update: $update) {
                success
                issue {
                    id
                    title
                    description
                    identifier
                    url
                    priority
                    status {
                        name
                    }
                    assignee {
                        id
                        name
                        email
                        active
                    }
                }
            }
        }
        """
        variables: dict = {"id": issue_id, "update": {}}
        if update.title is not None:
            variables["update"]["title"] = update.title
        if update.description is not None:
            variables["update"]["description"] = update.description

        data = await self._client.execute_async(query, variables)
        issue_data = data.get("issueUpdate", {}).get("issue")
        if not issue_data:
            raise ValueError("Failed to update issue")

        return self._parse_issue(issue_data)

    async def delete_issue(self, issue_id: str) -> bool:
        query = """
        mutation DeleteIssue($id: String!) {
            issueDelete(id: $id) {
                success
                lastSyncId
            }
        }
        """
        data = await self._client.execute_async(query, {"id": issue_id})
        return data.get("issueDelete", {}).get("success", False)

    def _parse_user(self, data: dict | None) -> User | None:
        if not data:
            return None
        return User(
            id=data["id"],
            name=data["name"],
            email=data.get("email", ""),
            active=data.get("active", False),
        )

    def _parse_issue(self, data: dict) -> Issue:
        return Issue(
            id=data["id"],
            title=data["title"],
            description=data.get("description"),
            identifier=data["identifier"],
            url=data["url"],
            priority=data.get("priority"),
            status=data.get("status", {}).get("name") if data.get("status") else None,
            assignee=self._parse_user(data.get("assignee")),
        )
