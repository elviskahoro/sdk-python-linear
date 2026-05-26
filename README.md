# gtm-linear

GTM workflow adapter for Linear. A Python SDK for interacting with Linear's GraphQL API with full type support using Strawberry GraphQL.

## Install

```bash
uv pip install gtm-linear
```

Or with pip:

```bash
pip install gtm-linear
```

## Installation from Source

```bash
git clone https://github.com/elviskahoro/sdk-python-linear.git
cd sdk-python-linear
uv sync
```

## Usage

### Basic Client Setup

```python
from gtm_linear import LinearClient, LinearQueries, LinearMutations

# Initialize the client with your Linear API key
# API keys can be created in Linear Settings > API > Personal API keys
# The key format is lin_api_... (no Bearer prefix needed)
async with LinearClient("lin_api_your_api_key_here") as client:
    queries = LinearQueries(client)
    mutations = LinearMutations(client)
```

### Querying Issues

```python
import asyncio
from gtm_linear import LinearClient, LinearQueries

async def get_issues():
    async with LinearClient("lin_api_your_api_key_here") as client:
        queries = LinearQueries(client)

        # Get a single issue by ID
        issue = await queries.get_issue("issue-id")
        if issue:
            print(f"Issue: {issue.identifier} - {issue.title}")

        # List issues for a team
        issues = await queries.list_issues("team-id", first=50)
        for issue in issues:
            print(f"{issue.identifier}: {issue.title}")

        # Search for issues
        results = await queries.search_issues("bug")
        for issue in results:
            print(f"{issue.identifier}: {issue.title}")

asyncio.run(get_issues())
```

### Creating and Updating Issues

```python
import asyncio
from gtm_linear import LinearClient, LinearMutations
from gtm_linear.generated_types import IssueCreateInput, IssueUpdateInput

async def manage_issues():
    async with LinearClient("lin_api_your_api_key_here") as client:
        mutations = LinearMutations(client)

        # Create a new issue
        input = IssueCreateInput(
            title="New bug found",
            teamId="team-id",
            description="Description of the bug"
        )
        issue = await mutations.create_issue(input)
        print(f"Created: {issue.identifier}")

        # Update an issue
        update = IssueUpdateInput(title="Updated title")
        updated = await mutations.update_issue(issue.id, update)
        print(f"Updated: {updated.title}")

        # Delete an issue
        success = await mutations.delete_issue(issue.id)
        print(f"Deleted: {success}")

asyncio.run(manage_issues())
```

### Error Handling

```python
from gtm_linear import LinearClient, LinearAPIError

try:
    with LinearClient("lin_api_key") as client:
        result = client.execute(query, variables)
except LinearAPIError as e:
    print(f"API Error: {e.message}")
    for error in e.errors:
        print(f"  - {error}")
```

## Development

### Running Tests

```bash
uv run pytest
```

Or with pytest directly:

```bash
pytest tests/ -v
```

### Type Checking

```bash
uv run mypy src/gtm_linear
```

## Status

Pre-alpha. Name reserved on PyPI; API surface coming soon.