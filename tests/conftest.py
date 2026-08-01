"""Shared test fixtures.

The payload factories live here rather than in each test module because both the
query and mutation suites need the same wire shapes, and they previously drifted
apart — the two copies of ``_issue_payload`` disagreed about nullability.
"""

from __future__ import annotations

from typing import Any

import pytest

from gtm_linear.client import LinearClient

API_URL = LinearClient.BASE_URL


def user_payload(user_id: str = "u-1") -> dict[str, Any]:
    """A Linear ``User`` as the SDK's UserFields fragment selects it."""
    return {
        "id": user_id,
        "name": "Alice",
        "email": "alice@example.com",
        "active": True,
    }


def issue_payload(issue_id: str = "iss-1", *, assignee: bool = True) -> dict[str, Any]:
    """A Linear ``Issue`` as the SDK's IssueFields fragment selects it.

    Note ``state`` is an object and is never null: Linear's schema types it
    ``WorkflowState!``. The old fixtures sent ``status: None``, which the API
    cannot actually return.
    """
    return {
        "id": issue_id,
        "identifier": "ENG-1",
        "title": "Hello",
        "description": "desc",
        "url": "https://linear.app/x/issue/ENG-1",
        "priority": 2,
        "state": {"id": "st-1", "name": "In Progress", "type": "started"},
        "assignee": user_payload() if assignee else None,
    }


def page_info_payload(
    *,
    has_next: bool = False,
    has_previous: bool = False,
    start: str | None = "cursor-a",
    end: str | None = "cursor-b",
) -> dict[str, Any]:
    """A Linear ``PageInfo`` as the SDK's PageInfoFields fragment selects it."""
    return {
        "hasNextPage": has_next,
        "hasPreviousPage": has_previous,
        "startCursor": start,
        "endCursor": end,
    }


@pytest.fixture
def api_url() -> str:
    return API_URL
