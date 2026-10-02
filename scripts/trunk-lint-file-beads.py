#!/usr/bin/env python3
# ruff: noqa: N999
"""Run `trunk check --all --show-existing` and file one bead per offending file.

Trunk's own output is the single source of truth: this script does not
re-implement any linter's rule set. It only (1) runs the check, (2) groups
the plain-text report by file path, and (3) upserts one bead per path so a
sweep-style cleanup ("fix the lint in file X") has a natural work unit.

The full raw trunk report is always written to one file
(`tmp/trunk-lint-report.txt` by default) -- that is the "one file has all the
issues" artifact everything else is derived from. Each created bead's
description is the excerpt for that path plus a pointer back to the report
file, so a bead never goes stale relative to trunk's own formatting.

By default `trunk` runs in the repo's activated Flox environment. Set
`RUN_WITH_DAGGER=1` to opt into the shared Dagger wrapper (same prebuilt Flox
toolchain image) -- useful for a reproducible CI-shaped run. `bd` itself is
never containerized: it is a local/host tool that talks to this checkout's
own beads DB, the same reasoning `beads-rig-sync_to.py` documents for why bd
subprocess calls stay outside Dagger.

`--fix` is opt-in, not the default: a bead-filing sweep is meant to be a
read-and-classify operation, and auto-mutating the working tree on every run
(especially under a schedule/cron) is a surprise an operator should choose,
not inherit.

Re-running is safe: each bead is tagged with the `trunk-lint` label and a
`trunk_file` metadata key equal to the path, so an existing open bead for a
path is updated (title/description refreshed) instead of duplicated.

`--prune-resolved` is opt-in, not the default: a path that no longer appears
in the report (because it was fixed, or the offending file was removed) can
have its bead closed automatically, but only when the underlying `trunk
check` run completed cleanly (exit 0 or 1) -- any other exit code aborts
before touching beads at all, since the captured output can't be trusted as a
complete report. This is opt-in because a caller that passes `--report` with
a narrower slice of the tree than usual (or a transient parse gap) would
otherwise look identical to "these files are fixed" and silently close beads
for issues that never went away.

Usage:
    scripts/trunk-lint-file-beads.py
    scripts/trunk-lint-file-beads.py --fix
    scripts/trunk-lint-file-beads.py --prune-resolved
    scripts/trunk-lint-file-beads.py --dry-run
    scripts/trunk-lint-file-beads.py --report tmp/trunk-lint-report.txt
    RUN_WITH_DAGGER=1 scripts/trunk-lint-file-beads.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from scripts.lib.uv_bootstrap import bootstrap_uv as _bootstrap_uv  # noqa: E402

if __name__ == "__main__":
    _bootstrap_uv(script_path=__file__, mode="python")

import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import typer  # noqa: E402

from scripts.lib.bd import BdCommandError, report_bd_failure, run_bd  # noqa: E402
from scripts.lib.container import (  # noqa: E402
    RUN_WITH_DAGGER,
    in_container_phase,
    run_in_container,
)
from scripts.lib.env import env_flag  # noqa: E402
from scripts.lib.flox import run as flox_run  # noqa: E402


def trunk_args(*, fix: bool) -> list[str]:
    args = ["trunk", "check", "--all", "--show-existing", "--no-progress"]
    if fix:
        args.insert(2, "--fix")
    return args


DEFAULT_REPORT_PATH = REPO_ROOT / "tmp" / "trunk-lint-report.txt"

BEAD_LABEL = "trunk-lint"
METADATA_KEY = "trunk_file"

EPIC_LABEL = "trunk-lint-epic"
EPIC_METADATA_KEY = "trunk_lint_epic"
EPIC_METADATA_VALUE = "sweep"
EPIC_TITLE = "[epic] trunk lint: sweep cleanup"

# Matches a flush-left "<path>" or "<path>:<line>:<col>" header line -- the
# table rows underneath it are always indented, so this never mistakes an
# issue row or a code-snippet line for a new file's header. Verified against
# real `trunk check` output: most findings render "<path>:<line>:<col>", but
# whole-file findings (e.g. a formatter's "Incorrect formatting") render only
# the bare path with no line:col suffix. The line:col group is therefore
# optional; parse_findings_by_file below only treats a bare-path match as an
# actual header when the next non-blank line looks like an issue row, so a
# summary/banner line that happens to have no leading whitespace (e.g.
# "Checked 1 file") is never mistaken for one.
_FILE_HEADER_RE = re.compile(r"^(?P<path>\S+?)(?::(?P<line>\d+):(?P<col>\d+))?\s*$")

# Matches an issue row's own leading "<line>:<col>  <severity>" columns, used
# both to confirm a bare-path header candidate and to produce a
# human-friendly count in the bead title.
_ISSUE_ROW_RE = re.compile(r"^\s*\d+:\d+\s+(low|medium|high|critical)\s", re.IGNORECASE)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class TrunkCommandError(RuntimeError):
    """`trunk check` could not be run at all (missing binary, transport failure)."""


@dataclass
class FileFindings:
    path: str
    block: str
    issue_count: int


def strip_ansi(text: str) -> str:
    """Drop color/cursor-control escapes so the header regex sees plain text."""
    return _ANSI_RE.sub("", text)


# `trunk check` exits 0 (clean) or 1 (issues found, --show-existing) for a
# completed run; any other code means the invocation itself misbehaved
# (crash, bad args, transport failure), so its output is not a trustworthy
# full report and must not drive bead reconciliation.
_CLEAN_RUN_RETURN_CODES = (0, 1)


@dataclass
class TrunkRunResult:
    output: str
    return_code: int | None  # None when the transport can't surface an exit code


def run_trunk_check(*, fix: bool) -> TrunkRunResult:
    """Run the trunk check and return its combined stdout+stderr as plain text.

    `trunk check` exits non-zero whenever it finds unresolved issues -- that
    is the expected, common case here, not a transport failure -- so this
    only raises when the *invocation itself* could not complete. The exit
    code is carried alongside the output so callers can tell a completed run
    (0 or 1) from a crash that happened to print something on stderr.
    """
    argv = trunk_args(fix=fix)
    if env_flag(RUN_WITH_DAGGER) and not in_container_phase():
        # Deferred import: the default Flox path must keep working in
        # environments that deliberately lack the Dagger SDK (see
        # scripts/lib/container.py's own deferred import for the same reason).
        import dagger

        try:
            output = run_in_container(
                repo_root=REPO_ROOT,
                argv=argv,
                capture=True,
            )
        except dagger.ExecError as exc:
            # ExecError is the documented carrier for "container command
            # exited non-zero" -- which includes trunk's expected exit 1 on
            # findings. Anything else (connection failure, image pull error)
            # propagates so it isn't mistaken for a completed report.
            combined = ((exc.stdout or "") + (exc.stderr or "")).strip()
            if not combined:
                msg = f"trunk check failed to run in the Dagger container: {exc}"
                raise TrunkCommandError(msg) from exc
            return TrunkRunResult(output=combined, return_code=exc.exit_code)
        return TrunkRunResult(output=output or "", return_code=0)

    try:
        output = (
            flox_run(
                argv,
                repo_root=REPO_ROOT,
                capture=True,
            )
            or ""
        )
    except subprocess.CalledProcessError as exc:
        combined = ((exc.stdout or "") + (exc.stderr or "")).strip()
        if not combined:
            msg = f"trunk check failed to run: {exc}"
            raise TrunkCommandError(msg) from exc
        return TrunkRunResult(output=combined, return_code=exc.returncode)
    return TrunkRunResult(output=output, return_code=0)


def _next_nonblank(lines: list[str], start: int) -> str | None:
    for candidate in lines[start:]:
        if candidate.strip():
            return candidate
    return None


def parse_findings_by_file(raw_output: str) -> list[FileFindings]:
    """Group trunk's report into one block of text per distinct file path.

    A header line ("<path>:<line>:<col>" or bare "<path>", flush left) can
    recur for the same path across different report sections (e.g. autofix
    suggestions followed by a plain issue table); every occurrence is
    appended to that path's running block, in the order trunk printed it, so
    nothing is dropped.

    A header with an explicit "<line>:<col>" is trusted on sight. A bare
    "<path>" (no line:col -- real for whole-file findings like a formatter's
    "Incorrect formatting") is only trusted as a header when the next
    non-blank line looks like an issue row, so a flush-left summary/banner
    line (e.g. "Checked 1 file") is never mistaken for a new file.
    """
    text = strip_ansi(raw_output)
    lines = text.splitlines()
    blocks: dict[str, list[str]] = {}
    order: list[str] = []
    current_path: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_path is None:
            return
        blocks.setdefault(current_path, [])
        if current_path not in order:
            order.append(current_path)
        blocks[current_path].append("\n".join(current_lines).rstrip())

    for index, line in enumerate(lines):
        header = _FILE_HEADER_RE.match(line)
        has_line_col = header is not None and header.group("line") is not None
        is_header = has_line_col or (
            header is not None
            and (nxt := _next_nonblank(lines, index + 1)) is not None
            and _ISSUE_ROW_RE.match(nxt) is not None
        )
        if is_header:
            assert header is not None  # noqa: S101 -- is_header implies a match above
            flush()
            current_path = header.group("path")
            current_lines = [line]
        elif current_path is not None:
            current_lines.append(line)
    flush()

    findings = []
    for path in order:
        block = "\n\n".join(part for part in blocks[path] if part)
        issue_count = sum(1 for line in block.splitlines() if _ISSUE_ROW_RE.match(line))
        findings.append(
            FileFindings(path=path, block=block, issue_count=max(issue_count, 1)),
        )
    return findings


class BdListParseError(RuntimeError):
    """`bd list --json` returned output that could not be parsed as JSON."""


def list_existing_beads() -> dict[str, str]:
    """Return {path: bead_id} for every open trunk-lint bead.

    Fetched once per run so both the per-file upsert and the stale-bead
    reconciliation pass work off the same snapshot. The script's "re-running
    is safe, beads are updated not duplicated" contract depends on this
    lookup actually finding existing beads, so a parse failure must not
    silently fall back to "no existing beads" -- that would look like a
    clean idempotent re-run while actually duplicating every bead. Raising
    lets the caller abort instead.
    """
    result = run_bd(["list", "--label", BEAD_LABEL, "--json"], cwd=REPO_ROOT)
    stdout = result.stdout.strip() if result.stdout else ""
    try:
        # An empty stdout is treated as "no beads" only when it is truly
        # empty; anything else must parse to a JSON list, otherwise a
        # truncated/garbled-but-non-empty response would silently look like
        # a fresh state and duplicate every bead on this run.
        issues = json.loads(stdout) if stdout else []
    except json.JSONDecodeError as exc:
        msg = f"bd list --json returned unparseable output: {exc}"
        raise BdListParseError(msg) from exc
    if not isinstance(issues, list):
        msg = f"bd list --json returned a JSON {type(issues).__name__}, expected a list"
        raise BdListParseError(msg)
    beads: dict[str, str] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            msg = f"bd list --json returned a non-object entry: {issue!r}"
            raise BdListParseError(msg)
        metadata = issue.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            msg = f"bd list --json entry has non-object metadata: {metadata!r}"
            raise BdListParseError(msg)
        path = metadata.get(METADATA_KEY) if metadata else None
        bead_id = issue.get("id")
        if path and bead_id:
            beads[path] = bead_id
    return beads


def _list_epic_candidates() -> list[dict[str, object]]:
    """Return every open bead matching the sweep epic's type/label/metadata.

    Filtering on ``--type epic`` (not just the label/metadata pair) means a
    malformed or manually created non-epic bead that happens to carry the
    same label and metadata is never mistaken for the sweep epic.
    """
    result = run_bd(
        [
            "list",
            "--type",
            "epic",
            "--label",
            EPIC_LABEL,
            "--metadata-field",
            f"{EPIC_METADATA_KEY}={EPIC_METADATA_VALUE}",
            "--json",
        ],
        cwd=REPO_ROOT,
    )
    stdout = result.stdout.strip() if result.stdout else ""
    try:
        issues = json.loads(stdout) if stdout else []
    except json.JSONDecodeError as exc:
        msg = f"bd list --json returned unparseable output: {exc}"
        raise BdListParseError(msg) from exc
    if not isinstance(issues, list):
        msg = f"bd list --json returned a JSON {type(issues).__name__}, expected a list"
        raise BdListParseError(msg)
    for issue in issues:
        if not isinstance(issue, dict):
            msg = f"bd list --json returned a non-object entry: {issue!r}"
            raise BdListParseError(msg)
        epic_id = issue.get("id")
        if not epic_id or not isinstance(epic_id, str):
            msg = f"bd list --json returned an entry with no usable id: {issue!r}"
            raise BdListParseError(msg)
    return issues


def _lowest_epic_id(candidates: list[dict[str, object]]) -> str:
    """Deterministically pick one epic id from every matching candidate.

    ``_list_epic_candidates`` already guarantees every entry has a non-empty
    string id, so this never raises -- it's kept separate so the "which one
    wins" tie-break logic has one place to live.
    """
    return min(str(issue["id"]) for issue in candidates)


def find_or_create_epic(*, dry_run: bool) -> str:
    """Find the sweep epic every file-bead should be parented under, or create it.

    Mirrors ``list_existing_beads``'s defensiveness: a non-empty response
    that fails to parse as a JSON list of objects must abort rather than be
    treated as "no epic yet", since that would silently spawn a duplicate
    epic on every run instead of reusing the one true sweep epic.

    ``bd`` has no create-if-not-exists primitive, so two concurrent sweep
    runs can both see "no epic yet" and each create one. Rather than assume
    that never happens, re-list after creating and deterministically settle
    on the lowest id among every matching epic (including the one just
    created), so two callers that both survive to the re-list step converge
    on the same winner without needing a lock. This narrows, but does not
    close, the race: two runs whose creates *and* re-lists both land before
    either write is visible to the other can still each pick their own epic.
    ``bd`` exposes no repository-level lock to close that window, and this
    script is a manual/cron sweep tool that is not expected to run
    concurrently with itself in practice, so the residual risk is accepted
    rather than engineered away. A loser epic is left open rather than
    auto-closed here; reconciling it is the same deliberate manual step the
    module docstring already carves out for ``--prune-resolved``.
    """
    candidates = _list_epic_candidates()
    if candidates:
        epic_id = _lowest_epic_id(candidates)
        typer.echo(f"→ reusing epic {epic_id}: {EPIC_TITLE}")
        return epic_id

    if dry_run:
        typer.echo(f"→ would create epic: {EPIC_TITLE}")
        return "<new-epic>"

    result = run_bd(
        [
            "create",
            "--title",
            EPIC_TITLE,
            "--labels",
            EPIC_LABEL,
            "--type",
            "epic",
            "--metadata",
            json.dumps({EPIC_METADATA_KEY: EPIC_METADATA_VALUE}),
            "--json",
        ],
        cwd=REPO_ROOT,
    )
    created = json.loads(result.stdout)

    # Re-list to catch a sibling process that created a competing epic
    # between this call's initial lookup and its own create above.
    settled_id = _lowest_epic_id(_list_epic_candidates())
    if settled_id != created["id"]:
        typer.echo(
            f"→ created epic {created['id']} but {settled_id} won the race, using it",
        )
        return settled_id
    typer.echo(f"→ created epic {created['id']}: {EPIC_TITLE}")
    return created["id"]


def bead_title(finding: FileFindings) -> str:
    plural = "" if finding.issue_count == 1 else "s"
    return f"trunk lint: {finding.issue_count} issue{plural} in {finding.path}"


def bead_description(finding: FileFindings, *, report_path: Path) -> str:
    return (
        f"`trunk check --all --show-existing` findings for `{finding.path}`.\n"
        f"Full report: {report_path}\n\n"
        "```\n"
        f"{finding.block}\n"
        "```\n"
    )


def upsert_bead(
    finding: FileFindings,
    *,
    report_path: Path,
    existing_beads: dict[str, str],
    epic_id: str | None,
    dry_run: bool,
) -> str:
    title = bead_title(finding)
    description = bead_description(finding, report_path=report_path)
    existing_id = existing_beads.get(finding.path)

    if dry_run:
        verb = "would update" if existing_id else "would create"
        return f"{verb} {existing_id or '<new>'}: {title}"

    if existing_id:
        # --metadata is deliberately not re-sent here: existing_id was looked
        # up via list_existing_beads() keyed on this exact METADATA_KEY/path
        # pair, so an update can never change it. Only a future migration of
        # the metadata scheme itself (e.g. absolute -> relative paths) would
        # need this refreshed, and that would touch every bead at once, not
        # one at a time through this per-file upsert.
        run_bd(
            [
                "update",
                existing_id,
                "--title",
                title,
                "--description",
                description,
            ],
            cwd=REPO_ROOT,
        )
        return f"updated {existing_id}: {title}"

    # Reaching here (not dry-run, no existing bead) means a new bead is
    # about to be created, and the caller only omits epic_id when it already
    # knows every finding matches an existing bead -- so a create-path call
    # always carries a real epic id.
    assert epic_id is not None, "epic_id must be set before creating a new bead"  # noqa: S101
    result = run_bd(
        [
            "create",
            "--title",
            title,
            "--description",
            description,
            "--labels",
            BEAD_LABEL,
            "--type",
            "task",
            "--metadata",
            json.dumps({METADATA_KEY: finding.path}),
            "--parent",
            epic_id,
            "--json",
        ],
        cwd=REPO_ROOT,
    )
    created = json.loads(result.stdout)
    return f"created {created['id']}: {title}"


def close_stale_beads(
    existing_beads: dict[str, str],
    *,
    current_paths: set[str],
    dry_run: bool,
) -> list[str]:
    """Close beads for files no longer present in the current trunk report."""
    summaries = []
    for path, bead_id in existing_beads.items():
        if path in current_paths:
            continue
        if dry_run:
            summaries.append(f"would close {bead_id}: {path} (resolved)")
            continue
        run_bd(["close", bead_id, "--reason", "resolved"], cwd=REPO_ROOT)
        summaries.append(f"closed {bead_id}: {path} (resolved)")
    return summaries


app = typer.Typer(
    add_completion=False,
    help=__doc__,
)


@app.command()
def main(
    report: Path = typer.Option(
        DEFAULT_REPORT_PATH,
        "--report",
        help="Where to write the full raw trunk report (the 'one file has all the issues' artifact).",
    ),
    dry_run: bool = typer.Option(  # noqa: FBT001
        default=False,
        help="Show what would be created/updated without writing to beads.",
    ),
    fix: bool = typer.Option(  # noqa: FBT001
        default=False,
        help="Pass --fix to trunk, auto-applying its fixes before filing beads "
        "for residual issues. Off by default so a bead-filing sweep never "
        "mutates the working tree unless explicitly asked to.",
    ),
    prune_resolved: bool = typer.Option(  # noqa: FBT001
        default=False,
        help="Close trunk-lint beads for paths no longer in the report. Off "
        "by default -- see the module docstring for why an unconditional "
        "prune is unsafe.",
    ),
) -> None:
    try:
        run_result = run_trunk_check(fix=fix)
    except TrunkCommandError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(run_result.output)
    typer.echo(f"→ wrote full trunk report to {report}")

    if run_result.return_code not in _CLEAN_RUN_RETURN_CODES:
        typer.echo(
            f"error: trunk exited {run_result.return_code} (not a clean 0/1 run); "
            f"report may be partial -- see {report}, skipping all bead changes",
            err=True,
        )
        raise typer.Exit(run_result.return_code or 1)

    findings = parse_findings_by_file(run_result.output)

    if run_result.return_code == 1 and not findings:
        # Exit 1 means trunk itself says there are unresolved issues, so a
        # parser that found none is untrustworthy (format drift, a change to
        # trunk's output shape) rather than a genuinely clean tree -- treat
        # it the same as a non-clean run rather than filing zero beads and
        # potentially pruning every existing one as "resolved".
        typer.echo(
            "error: trunk exited 1 (issues found) but no findings were parsed "
            f"from its output -- see {report}; this usually means trunk's "
            "output format changed. Skipping all bead changes.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        existing_beads = list_existing_beads()

        if not findings:
            typer.echo("✓ no lint issues found")
        else:
            # Only find/create the epic when at least one finding will
            # actually produce a new bead -- a run where every finding
            # matches an already-linked existing bead has nothing to parent,
            # and epic lookup/creation would just be backlog clutter.
            epic_id = None
            if any(finding.path not in existing_beads for finding in findings):
                epic_id = find_or_create_epic(dry_run=dry_run)
            typer.echo(f"→ {len(findings)} file(s) with findings")
            for finding in findings:
                summary = upsert_bead(
                    finding,
                    report_path=report,
                    existing_beads=existing_beads,
                    epic_id=epic_id,
                    dry_run=dry_run,
                )
                typer.echo(f"  {summary}")

        if prune_resolved:
            stale_summaries = close_stale_beads(
                existing_beads,
                current_paths={finding.path for finding in findings},
                dry_run=dry_run,
            )
            if stale_summaries:
                typer.echo(
                    f"→ {len(stale_summaries)} resolved file(s), closing bead(s)",
                )
                for summary in stale_summaries:
                    typer.echo(f"  {summary}")
    except BdCommandError as exc:
        report_bd_failure(exc)
        raise typer.Exit(1) from None
    except BdListParseError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
