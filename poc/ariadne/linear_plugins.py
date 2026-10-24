"""ariadne-codegen plugins needed to keep the SDK's public attribute names.

Runs inside the isolated generator environment (``poc/ariadne/.venv``), never at
SDK runtime - so ``ariadne_codegen`` only resolves there, not in the root venv the
repo's type checkers use. Check this file with
``pyright --pythonpath poc/ariadne/.venv/bin/python poc/ariadne/linear_plugins.py``.
"""

from __future__ import annotations

import builtins
import keyword

from ariadne_codegen.plugins.base import Plugin  # pyrefly: ignore[missing-import]
from graphql import FieldNode, GraphQLInputField


def _shadows_builtin_type(name: str) -> bool:
    return isinstance(getattr(builtins, name, None), type)


class PreserveBuiltinNamesPlugin(Plugin):  # pyright: ignore[reportUntypedBaseClass]
    """Undo Ariadne's ``type`` -> ``type_`` rename on model fields.

    Ariadne appends ``_`` to any name that shadows a builtin *type* (``type``,
    ``filter``, ``list``, ...). That is harmless for a fresh client, but the SDK's
    models have always exposed ``issue.state.type`` and
    ``IssueRelationCreateInput(type=...)``, and the README documents them. A
    Pydantic field may shadow a builtin, so result fields (``FieldNode``) and input
    fields (``GraphQLInputField``) get the GraphQL spelling back. Method
    arguments keep Ariadne's ``filter_``: the handwritten wrapper owns those
    signatures. Real keywords (``from`` -> ``from_``) are left renamed.
    """

    def process_name(self, name: str, node: object = None) -> str:
        """Strip the builtin-shadowing underscore from model field names."""
        if not isinstance(node, (FieldNode, GraphQLInputField)):
            return name
        original = name.removesuffix("_")
        if (
            original != name
            and _shadows_builtin_type(original)
            and not keyword.iskeyword(original)
        ):
            return original
        return name
