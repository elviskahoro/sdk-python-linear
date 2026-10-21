"""Root conftest.py - loads before pytest plugins initialize."""

from __future__ import annotations

# Set up httpx2 aliasing BEFORE anything imports httpx
try:
    from httpx2 import alias_httpx

    alias_httpx()
except RuntimeError:
    # httpx is already imported; respx may have imported it before the alias could be set up
    pass
