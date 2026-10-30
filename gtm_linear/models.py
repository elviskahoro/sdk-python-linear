"""The base model every generated Linear type inherits.

This is the only hand-written model in the SDK. Everything else under
``gtm_linear/_generated/`` is produced from Linear's schema by scripts/codegen.py.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class LinearModel(BaseModel):
    """Base model for API payloads, and the base every generated model inherits.

    ``extra="ignore"`` means a field Linear adds to a response is dropped rather
    than raising, so the SDK keeps working across additive API changes.

    ``alias_generator`` bridges the naming gap in both directions: Linear speaks
    camelCase on the wire, generated models expose snake_case attributes. Parsing
    works on the alias, and ``model_dump(by_alias=True)`` writes camelCase back out.
    ``populate_by_name`` additionally allows constructing models with the Python
    field names, which is what callers actually type.
    """

    model_config = ConfigDict(
        extra="ignore",
        alias_generator=to_camel,
        populate_by_name=True,
        validate_assignment=True,
    )


def dump_variables(model: BaseModel) -> dict[str, Any]:
    """Serialize a variables or input model the way Linear expects it.

    ``exclude_unset`` separates "omit" from "send null": a field never passed is
    left out, while one explicitly set to None goes out as ``null``.
    """
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)
