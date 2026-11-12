"""Portable CI core for sdk-python-linear (plain Dagger SDK program, no module).

Run via ``ci/run.sh <subcommand>`` (= ``dagger run python ci/main.py ...``).
Subcommands: test, lean-install, tach, all.  Same code path locally and in RWX;
the only differences come from env vars (``CI_OUT_DIR``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
import dagger
from dagger import dag

PYTHON_IMAGE = "python:3.13.1-slim-bookworm"
UV_IMAGE = "ghcr.io/astral-sh/uv:0.11.29"
REPO = Path(__file__).resolve().parent.parent
# Never upload VCS state, venvs, caches, CI-only dirs or prior outputs.
EXCLUDE = [
    ".git", ".venv", "**/.venv", "**/__pycache__", ".context", ".pytest_cache",
    ".ruff_cache", "tmp", "dist", "ci/.venv", ".flox", ".beads",
]


def base() -> dagger.Container:
    """Toolchain layer: python + uv + jq (least-changing first)."""
    uv_bin = dag.container().from_(UV_IMAGE).file("/uv")
    return (
        dag.container()
        .from_(PYTHON_IMAGE)
        .with_exec(["sh", "-c",
                    "apt-get update -qq && apt-get install -y -qq --no-install-recommends jq git ca-certificates "
                    "&& rm -rf /var/lib/apt/lists/*"])
        .with_file("/usr/local/bin/uv", uv_bin, permissions=0o755)
        .with_env_variable("UV_LOCKED", "1")
        .with_env_variable("UV_PYTHON", "3.13")
        .with_env_variable("UV_PYTHON_DOWNLOADS", "never")
        .with_env_variable("UV_LINK_MODE", "copy")
        .with_mounted_cache("/root/.cache/uv", dag.cache_volume("gtm-linear-uv"))
    )


def project(src: dagger.Directory) -> dagger.Container:
    """Deps layer (lockfiles only) then full source on top."""
    manifests = dag.directory().with_file("pyproject.toml", src.file("pyproject.toml")).with_file(
        "uv.lock", src.file("uv.lock"))
    return (
        base()
        .with_workdir("/src")
        .with_directory("/src", manifests)
        .with_exec(["uv", "sync", "--no-install-project"])
        .with_directory("/src", src)
        .with_exec(["uv", "sync"])
    )


async def test(src: dagger.Directory, out: Path) -> int:
    ctr = project(src).with_env_variable("REQUIRE_JQ", "1").with_exec(["mkdir", "-p", "tmp"])
    junit = ("-o", "junit_family=xunit1")
    first = ctr.with_exec(
        ["uv", "run", "pytest", "-q", *junit, "--junit-xml=tmp/junit-sdk-python-linear-pytest.xml"],
        expect=dagger.ReturnType.ANY,
    )
    code = await first.exit_code()
    final = first
    if code != 0:  # one retry of failures only, mirrors .captain/config.yml retries
        final = first.with_exec(
            ["uv", "run", "pytest", "-q", *junit, "--last-failed",
             "--junit-xml=tmp/junit-sdk-python-linear-pytest-retry.xml"],
            expect=dagger.ReturnType.ANY,
        )
        code = await final.exit_code()
    print(await final.stdout())
    await final.directory("/src/tmp").export(str(out))
    if code != 0:
        print(f"pytest failed (exit {code})", file=sys.stderr)
        return code
    checked = final.with_exec(["uv", "run", "python", "scripts/gen_operations.py", "--check"])
    checked = checked.with_exec(["uv", "run", "python", "scripts/codegen.py", "--check"])
    print(await checked.stdout())
    return 0


async def lean_install(src: dagger.Directory, _out: Path) -> int:
    wheel_src = dag.directory()
    for p in ("pyproject.toml", "uv.lock", "README.md", "LICENSE"):
        wheel_src = wheel_src.with_file(p, src.file(p))
    wheel_src = wheel_src.with_directory("gtm_linear", src.directory("gtm_linear"))
    script = r"""set -euo pipefail
uv build --wheel
uv venv -p 3.13 /tmp/lean
VIRTUAL_ENV=/tmp/lean uv pip install dist/*.whl
lean_list="$(VIRTUAL_ENV=/tmp/lean uv pip list)"
if grep -qi strawberry <<<"${lean_list}"; then
  echo "strawberry leaked into the runtime dependency tree"; exit 1
fi
/tmp/lean/bin/python -c "import gtm_linear; print(gtm_linear.__version__)"
"""
    ctr = base().with_workdir("/src").with_directory("/src", wheel_src).with_exec(["bash", "-c", script])
    print(await ctr.stdout())
    return 0


async def tach(src: dagger.Directory, _out: Path) -> int:
    ctr = project(src).with_exec(["uv", "run", "tach", "check-external"])
    print(await ctr.stdout())
    return 0


CHECKS = {"test": test, "lean-install": lean_install, "tach": tach}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("check", choices=[*CHECKS, "all"])
    ap.add_argument("--repo", default=str(REPO))
    args = ap.parse_args()
    out = Path(os.environ.get("CI_OUT_DIR", REPO / "tmp"))
    names = list(CHECKS) if args.check == "all" else [args.check]
    rc = 0
    async with dagger.connection(dagger.Config(log_output=sys.stderr)):
        src = dag.host().directory(args.repo, exclude=EXCLUDE)
        for name in names:
            try:
                code = await CHECKS[name](src, out)
            except dagger.ExecError as e:
                print(f"[{name}] FAILED: {e.message}", file=sys.stderr)
                code = e.exit_code or 1
            print(f"[{name}] {'ok' if code == 0 else f'exit {code}'}", file=sys.stderr)
            rc = rc or code
    return rc


if __name__ == "__main__":
    sys.exit(anyio.run(main))
