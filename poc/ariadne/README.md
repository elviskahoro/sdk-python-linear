# Ariadne Codegen proof of concept

An evaluation of replacing `scripts/codegen.py` (Strawberry codegen plus a Pydantic
plugin) with [ariadne-codegen](https://github.com/mirumee/ariadne-codegen). It is
isolated: nothing under `gtm_linear/` imports it, it is not shipped (wheel/sdist
exclude `/poc`), and the default `uv run pytest` does not collect it.

Tested: **ariadne-codegen 0.19.0** (BSD-3-Clause, released 2026-08-28) on Python
3.11, pydantic 2.13, against the pinned `schema/linear.graphql` and the 18 real
`operations/*.graphql`.

## Commands

```bash
uv sync                                               # root env, as usual
uv run python poc/ariadne/generate.py                 # regenerate linear_ariadne/
uv run python poc/ariadne/generate.py --check         # fail on drift (like codegen.py --check)
uv run python poc/ariadne/generate.py --schema-only   # custom-operation client -> build/ (not committed)
uv run pytest poc/ariadne/tests                       # 53 compatibility tests
```

The generator runs in its own locked env (`poc/ariadne/pyproject.toml` +
`uv.lock`, created on first use by `uv run --project poc/ariadne`). It cannot share
the root env: ariadne-codegen requires `graphql-core<3.3` and `httpx~=0.28`. The root
dev group pins `graphql-core>=3.3,<3.4`, because strawberry-graphql 0.328 needs it,
and the SDK runs on `httpx2`. `test_typing.py` fetches pyright 1.1.414 (the version
`.trunk/trunk.yaml` pins) through `uv tool run`.

## Layout

| Path                    | What                                                                                                                                                                             |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pyproject.toml`        | Generator env and the `[tool.ariadne-codegen]` config.                                                                                                                           |
| `generate.py`           | Generate, `--check`, and `--schema-only` (the generator always runs from this dir, so output is machine-independent).                                                            |
| `linear_base_client.py` | Custom base client. Ariadne's `execute`/`get_data` protocol runs on top of `gtm_linear.LinearClient`, so auth, httpx2, timeouts, lifecycle, and the error contract don't change. |
| `linear_plugins.py`     | `process_name` plugin that keeps `type` (Ariadne would emit `type_`).                                                                                                            |
| `linear_scalars.py`     | `JSONObject = dict[str, Any]` for the scalar map.                                                                                                                                |
| `linear_ariadne/`       | **Generated**, committed, lint-ignored. Operation-based client.                                                                                                                  |
| `build/`                | **Generated**, gitignored. Schema-only client (~55k lines / 2.3 MB).                                                                                                             |
| `tests/`                | Behavioural comparison against the current SDK.                                                                                                                                  |

## Findings

| Requirement                         | Current SDK                   | Ariadne, operation-based (+ base client + plugin)  | Ariadne, schema-only                               | Evidence                                                                        |
| ----------------------------------- | ----------------------------- | -------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------- |
| All 18 operations                   | yes                           | same document, variables, parsed data              | hand-built per call                                | `test_same_document_variables_and_result[*]`                                    |
| Validated against the full schema   | yes                           | yes, fails on a bad op                             | n/a (no documents)                                 | `test_invalid_operation_fails_generation`                                       |
| Shared fragment types               | hoisting code                 | native (aliases to one class)                      | n/a                                                | `fragments.py`                                                                  |
| Typed args                          | yes                           | yes                                                | yes (builder args)                                 | `test_typing.py`                                                                |
| Typed results                       | yes                           | yes                                                | **no: `dict[str, Any]`**                           | `test_revealed_types`                                                           |
| Omitted vs explicit null (args)     | always sends `null`           | `UNSET` omits, `None` sends null                   | **`None` silently dropped**                        | `test_optional_argument_*`, `test_explicit_null_argument_is_silently_dropped`   |
| Omitted vs null (input fields)      | `exclude_unset`               | same                                               | same                                               | `test_update_explicit_null_vs_unset_reaches_the_wire`                           |
| Enums / DateTime / JSON scalars     | yes                           | same wire values and Python types                  | wire-level only                                    | `test_enums_*`, `test_custom_scalars_*`                                         |
| Aliases, inline fragments, typename | via operations                | via operations                                     | `.alias()` and `.on()`; no `__typename` helper     | `schema_only_probe.py`                                                          |
| Opaque `IssueFilter` dicts          | opaque (`OPAQUE_INPUTS`)      | typed closure (90 models); dict passes through     | typed                                              | `test_opaque_filter_dict_vs_typed_issue_filter`                                 |
| Error contract (`LinearAPIError`)   | yes                           | identical (delegates)                              | **differs**: 400 auth hides codes, bare `KeyError` | `test_*errors*`, `test_stock_error_handling_*`                                  |
| Timeout / auth / lifecycle          | `LinearClient`                | identical                                          | stock httpx, 5 s default                           | `test_timeout_*`, `test_context_manager_*`                                      |
| Pagination helper                   | `paginate()`                  | works unchanged (structural protocol)              | manual                                             | `test_existing_paginate_drives_ariadne_connections`                             |
| Runtime deps                        | pydantic, httpx2              | pydantic only (+ gtm_linear)                       | httpx, **graphql-core <3.3**                       | `test_generated_client_needs_no_*`, `test_runtime_needs_graphql_core_below_3_3` |
| Deterministic regen + `--check`     | yes                           | yes                                                | yes                                                | `test_unchanged_inputs_regenerate_byte_identically`                             |
| Model base = `LinearModel`          | yes                           | **no** (own `BaseModel`)                           | no                                                 | `test_known_type_identity_differences`                                          |
| Enum type                           | `Enum`                        | **`str, Enum`**                                    | `str, Enum`                                        | same                                                                            |
| `validate_assignment`               | off                           | **on**                                             | on                                                 | same                                                                            |
| Strawberry mirror (`[strawberry]`)  | `_schema.py`                  | **not produced**                                   | not produced                                       | n/a                                                                             |
| Import time                         | ~0.25 s (`import gtm_linear`) | ~0.17 s with `defer_model_build` (~0.87 s without) | n/a                                                | `python -X importtime`                                                          |

Upstream health, as of 2026-10-24: the last release was 0.19.0 (2026-08-28), CI on
`main` has been red since 2026-09-09,
[graphql-core 3.3 support](https://github.com/mirumee/ariadne-codegen/issues/470) is
still open, and a
[maintenance-status issue](https://github.com/mirumee/ariadne-codegen/issues/472)
is open.

Not verified: live Linear requests (no credentials here), sync generation
(`async_client = false`; the SDK's sync path is `asyncio.run` in `LinearWorkflow`),
and subscriptions and uploads (the SDK uses neither).

## Documentation consulted (tag 0.19.0)

- [README](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/README.md)
- [Configuration reference](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/03-reference/01-configuration.md)
- [Generated code dependencies](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/03-reference/02-generated-code-dependencies.md)
- [Custom operation builder](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/02-guides/12-custom-operation-builder.md)
- [Custom scalars](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/02-guides/06-custom-scalars.md)
- [Using the generated client](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/02-guides/03-using-generated-client.md)
- [Async vs sync](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/02-guides/10-async-vs-sync.md)
- [Plugin hooks](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/04-plugins/03-hooks.md)
- [Import performance](https://github.com/mirumee/ariadne-codegen/blob/0.19.0/docs/02-guides/13-improving-import-performance.md)
