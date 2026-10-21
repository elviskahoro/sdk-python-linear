"""Root conftest.py: installs the ``httpx2`` process-wide alias.

``respx>=0.21`` registers ``respx.plugin`` as a ``pytest11`` setuptools entry
point, which pytest auto-loads before any conftest runs and which imports real
``httpx`` into ``sys.modules`` at that point. After that, ``alias_httpx()``
refuses to run (raises ``RuntimeError("httpx was already imported…")``), so the
swap would silently fail and respx would patch the wrong module. The pytest
config disables that plugin with ``-p no:respx``; ``import respx`` in test
modules still works.

The alias call is deliberately NOT wrapped in try/except: a partially-applied
alias is the worst case (respx patches real ``httpx`` while the SDK uses
``httpx2``, mocks silently miss, errors reach the real Linear API). Fail loud
instead of swallowing.
"""

from __future__ import annotations

from httpx2 import alias_httpx

alias_httpx()
