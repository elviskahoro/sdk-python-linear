"""Regression pins for the two #152 model behaviors that still ship.

These assertions used to live in ``tests/test_types.py`` alongside the
Strawberry schema mirror; the mirror is retired (#155) but both behaviors
remain part of the public surface, so the pins keep a dependency-light home:
a regeneration or model-config change that drops the ``(str, Enum)`` base
(which ``--json`` serialization relies on) or assignment validation must fail
CI here rather than silently change shipped semantics.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from gtm_linear._generated.CreateIssueRelation import IssueRelationType
from gtm_linear.models import LinearModel


def test_generated_enums_are_str_enums() -> None:
    assert IssueRelationType.blocks == "blocks"
    assert json.dumps(IssueRelationType.blocks) == '"blocks"'


def test_models_validate_assignment() -> None:
    class M(LinearModel):
        n: float

    m = M(n=1)
    with pytest.raises(ValidationError):
        m.n = "high"  # type: ignore[assignment]
