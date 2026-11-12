#!/usr/bin/env bash
# Run a CI check locally or in RWX: ci/run.sh {test|lean-install|tach|all}
set -euo pipefail
cd "$(dirname "$0")/.."
exec dagger run uv run --project ci python ci/main.py "$@"
