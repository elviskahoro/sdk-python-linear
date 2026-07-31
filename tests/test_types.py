import strawberry

from gtm_linear import IssueCreateInput, IssueUpdateInput


@strawberry.type
class _Query:
    @strawberry.field
    def ping(self) -> str:
        return "pong"


@strawberry.type
class _Mutation:
    @strawberry.mutation
    def create_issue(self, input: IssueCreateInput) -> str:
        return input.title

    @strawberry.mutation
    def update_issue(self, input: IssueUpdateInput) -> str:
        return input.title or ""


def test_issue_input_schema_uses_linear_compatible_types() -> None:
    schema = strawberry.Schema(query=_Query, mutation=_Mutation)
    sdl = str(schema)

    assert "teamId: String!" in sdl  # noqa: S101
    assert "labelIds: [String!]" in sdl  # noqa: S101
    assert "assigneeId: String" in sdl  # noqa: S101
    assert "projectId: String" in sdl  # noqa: S101
    assert "stateId: String" in sdl  # noqa: S101
    assert "priority: Int" in sdl  # noqa: S101
    assert "description: String" in sdl  # noqa: S101
