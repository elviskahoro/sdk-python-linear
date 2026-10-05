"""Regression tests for the weekly Dagger publisher release check."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "dagger_ref_drift.sh"
REPO = "elviskahoro/sdk-python-publish-to-pypi"
ISSUE_REPO = "elviskahoro/sdk-python-linear"
PINNED_SHA = "a" * 40
RETAGGED_SHA = "b" * 40
ANNOTATED_TAG_SHA = "c" * 40
ISSUE_MARKER = "<!-- sdk-python-linear:dagger-publisher-drift -->"
ISSUE_TITLE = "Dagger publish module ref is stale"
HAS_JQ = shutil.which("jq") is not None
requires_jq = pytest.mark.skipif(
    not HAS_JQ and os.environ.get("REQUIRE_JQ") != "1",
    reason="jq-based GH filter integration unless REQUIRE_JQ=1",
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_check(
    tmp_path: Path,
    *,
    pin: str = "v0.2.2",
    refs: tuple[str, ...] = (f"{PINNED_SHA} refs/tags/v0.2.2",),
    open_issues: str = "0",
    open_issue_body: str = "",
    open_issue_number: str = "123",
    marked_issue_number: str | None = None,
    marked_issue_body: str | None = None,
    second_pin: str | None = None,
    git_exit_code: int = 0,
    gh_issue_list_exit_code: int = 0,
    expected_sha: str = PINNED_SHA,
    active_sha: str | None = None,
    include_sha_record: bool = True,
    sha_record: str | None = None,
    verify_pin_only: bool = False,
    gh_token: str | None = "test-token",
    include_trailing_sha_note: bool = False,
    comment_pin: str | None = None,
    run_real_jq: bool = False,
    final_newline: bool = True,
    other_issues: tuple[tuple[str, str, str], ...] = (),
    extra_args: tuple[str, ...] = (),
    use_real_workflow: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
    if run_real_jq and not HAS_JQ:
        pytest.fail("jq is required for these GitHub issue-filter integration tests")
    workflow = tmp_path / ".github/workflows/pypi.yml"
    cwd = REPO_ROOT if use_real_workflow else tmp_path
    if not use_real_workflow:
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow_contents = ""
        if include_sha_record:
            trailing_note = " expected" if include_trailing_sha_note else ""
            workflow_contents += f"# publisher-module-sha: {pin} {sha_record or expected_sha}{trailing_note}\n"
        if comment_pin is not None:
            workflow_contents += f"# Example ref: github.com/{REPO}@{comment_pin}\n"
        workflow_contents += (
            f"dagger -m github.com/{REPO}@{active_sha or expected_sha} call build --source .\n"
        )
        if second_pin is not None:
            workflow_contents += (
                f"dagger -m github.com/{REPO}@{second_pin} call build --source .\n"
            )
        if not final_newline:
            workflow_contents = workflow_contents.rstrip("\n")
        workflow.write_text(workflow_contents)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    refs_file = tmp_path / "refs.txt"
    refs_file.write_text("\n".join(refs) + "\n")
    effective_marked_issue_body = marked_issue_body or open_issue_body
    if marked_issue_number is None:
        marked_issue_number = (
            open_issue_number if ISSUE_MARKER in effective_marked_issue_body else ""
        )
    issues_file = tmp_path / "issues.json"
    issue_records: list[dict[str, object]] = []
    marked_issue_numbers = marked_issue_number.splitlines()
    if open_issues != "0":
        if open_issue_number not in marked_issue_numbers:
            issue_records.append(
                {
                    "number": int(open_issue_number),
                    "title": ISSUE_TITLE,
                    "body": open_issue_body,
                },
            )
        issue_records.extend(
            {
                "number": int(issue_number),
                "title": ISSUE_TITLE,
                "body": effective_marked_issue_body,
            }
            for issue_number in marked_issue_numbers
            if issue_number
        )
    issue_records.extend(
        {"number": int(number), "title": title, "body": body}
        for number, title, body in other_issues
    )
    issues_file.write_text(json.dumps(issue_records))
    git_args_file = tmp_path / "git-args.txt"
    capture_file = tmp_path / "gh-calls.txt"
    gh_query_file = tmp_path / "gh-queries.txt"
    _write_executable(
        bin_dir / "git",
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$TEST_GIT_ARGS"\n'
        'if [ "$TEST_GIT_EXIT_CODE" -ne 0 ]; then echo "mock ls-remote failure" >&2; exit "$TEST_GIT_EXIT_CODE"; fi\n'
        'cat "$TEST_TAG_REFS"\n',
    )
    _write_executable(
        bin_dir / "gh",
        "#!/bin/sh\n"
        'if [ "$1 $2" = "issue list" ]; then\n'
        '  printf "%s\\n" "$*" >> "$TEST_GH_QUERIES"\n'
        '  if [ "$TEST_GH_LIST_EXIT_CODE" -ne 0 ]; then echo "mock issue lookup failure" >&2; exit "$TEST_GH_LIST_EXIT_CODE"; fi\n'
        '  if [ "$TEST_GH_REAL_JQ" = "1" ]; then\n'
        "    jq_filter=\n"
        '    while [ "$#" -gt 0 ]; do if [ "$1" = "--jq" ]; then shift; jq_filter=$1; break; fi; shift; done\n'
        '    jq -r "$jq_filter" "$TEST_GH_ISSUES_JSON"\n'
        '    exit "$?"\n'
        "  fi\n"
        '  if [ "$TEST_OPEN_ISSUES" != "0" ]; then\n'
        '    if ! printf "%s\\n" "$TEST_MARKED_ISSUE_NUMBER" | grep -Fxq "$TEST_OPEN_ISSUE_NUMBER"; then printf "%s\\t%s\\n" "$TEST_OPEN_ISSUE_NUMBER" "$TEST_OPEN_ISSUE_BODY_B64"; fi\n'
        '    printf "%s\\n" "$TEST_MARKED_ISSUE_NUMBER" | while IFS= read -r issue_number; do [ -n "$issue_number" ] || continue; printf "%s\\t%s\\n" "$issue_number" "$TEST_MARKED_ISSUE_BODY_B64"; done\n'
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1 $2" = "issue edit" ]; then printf "%s\\n" "$*" >> "$TEST_GH_CAPTURE"; exit 0; fi\n'
        'if [ "$1 $2" = "issue create" ]; then printf "%s\\n" "$*" >> "$TEST_GH_CAPTURE"; exit 0; fi\n'
        'if [ "$1 $2" = "issue close" ]; then printf "%s\\n" "$*" >> "$TEST_GH_CAPTURE"; exit 0; fi\n'
        "exit 2\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "TEST_GH_CAPTURE": str(capture_file),
            "TEST_GH_QUERIES": str(gh_query_file),
            "TEST_GIT_ARGS": str(git_args_file),
            "TEST_OPEN_ISSUES": open_issues,
            "TEST_OPEN_ISSUE_NUMBER": open_issue_number,
            "TEST_MARKED_ISSUE_NUMBER": marked_issue_number,
            "TEST_OPEN_ISSUE_BODY_B64": base64.b64encode(
                open_issue_body.encode(),
            ).decode(),
            "TEST_MARKED_ISSUE_BODY_B64": base64.b64encode(
                effective_marked_issue_body.encode(),
            ).decode(),
            "TEST_GH_ISSUES_JSON": str(issues_file),
            "TEST_GH_REAL_JQ": str(int(run_real_jq)),
            "TEST_TAG_REFS": str(refs_file),
            "TEST_GIT_EXIT_CODE": str(git_exit_code),
            "TEST_GH_LIST_EXIT_CODE": str(gh_issue_list_exit_code),
        },
    )
    if gh_token is not None:
        environment["GH_TOKEN"] = gh_token
    else:
        environment.pop("GH_TOKEN", None)
    check_args = ["bash", str(CHECK_SCRIPT)]
    if verify_pin_only:
        check_args.append("--verify-pin")
    check_args.extend(extra_args)
    result = subprocess.run(
        check_args,
        cwd=cwd,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    return (
        result,
        capture_file.read_text() if capture_file.exists() else "",
        git_args_file.read_text() if git_args_file.exists() else "",
        gh_query_file.read_text() if gh_query_file.exists() else "",
    )


def test_matching_pin_and_latest_tag_are_clean(tmp_path: Path) -> None:
    result, gh_calls, git_args, gh_queries = _run_check(
        tmp_path,
        comment_pin="v99.0.0",
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{RETAGGED_SHA} refs/tags/v99.0.0^{{}}",
            f"{RETAGGED_SHA} refs/tags/not-a-release",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "pinned=v0.2.2 latest_release=v0.2.2" in result.stdout
    assert gh_calls == ""
    assert git_args.splitlines() == [
        "ls-remote",
        "--tags",
        f"https://github.com/{REPO}",
        "refs/tags/v*",
    ]
    assert "--state open --limit 1000" in gh_queries
    assert "select(.title == env.DAGGER_REF_DRIFT_ISSUE_TITLE)" in gh_queries


def test_annotated_tag_uses_peeled_commit_sha(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{ANNOTATED_TAG_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.2^{{}}",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "checked" not in result.stdout
    assert "pinned=v0.2.2 latest_release=v0.2.2" in result.stdout
    assert gh_calls == ""


def test_missing_tag_sha_record_fails_clearly(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, include_sha_record=False)

    assert result.returncode != 0
    assert "expected exactly one publisher-module-sha record" in result.stderr
    assert gh_calls == ""


def test_malformed_tag_sha_record_fails_clearly(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, sha_record="not-a-sha")

    assert result.returncode != 0
    assert "must be a 40-character lowercase SHA" in result.stderr
    assert gh_calls == ""


def test_active_module_ref_must_match_renovate_sha_record(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        active_sha=RETAGGED_SHA,
    )

    assert result.returncode != 0
    assert "pypi.yml records" in result.stderr
    assert gh_calls == ""


def test_tag_sha_record_rejects_trailing_comment_text(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, include_trailing_sha_note=True)

    assert result.returncode != 0
    assert "expected exactly one publisher-module-sha record" in result.stderr
    assert gh_calls == ""


def test_publish_pin_verification_is_offline_and_needs_no_token(tmp_path: Path) -> None:
    result, gh_calls, git_args, gh_queries = _run_check(
        tmp_path,
        refs=(f"{RETAGGED_SHA} refs/tags/v0.2.2",),
        verify_pin_only=True,
        gh_token=None,
    )

    assert result.returncode == 0, result.stderr
    assert f"verified {REPO}@v0.2.2 recorded commit_sha={PINNED_SHA}" in result.stdout
    assert gh_calls == ""
    assert git_args == ""
    assert gh_queries == ""


def test_real_pypi_workflow_pin_and_sha_parse_in_publish_mode(
    tmp_path: Path,
) -> None:
    workflow_contents = (REPO_ROOT / ".github/workflows/pypi.yml").read_text()
    pin_pattern = rf"github\.com/{re.escape(REPO)}@([0-9a-f]{{40}})"
    pin_match = re.search(pin_pattern, workflow_contents)
    sha_match = re.search(
        r"^\s*# publisher-module-sha: (v\d+\.\d+\.\d+) ([0-9a-f]{40})$",
        workflow_contents,
        re.MULTILINE,
    )
    assert pin_match is not None
    assert sha_match is not None
    active_sha = pin_match.group(1)
    pin = sha_match.group(1)
    sha = sha_match.group(2)
    assert active_sha == sha
    check_step = "run: bash scripts/dagger_ref_drift.sh --verify-pin"
    build_step = f"dagger -m github.com/{REPO}@{sha} call build"
    assert workflow_contents.index(check_step) < workflow_contents.index(build_step)
    result, gh_calls, git_args, _ = _run_check(
        tmp_path,
        pin=pin,
        expected_sha=sha,
        active_sha=active_sha,
        refs=(f"{sha} refs/tags/{pin}",),
        verify_pin_only=True,
        gh_token=None,
        use_real_workflow=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"verified {REPO}@{pin} recorded commit_sha={sha}" in result.stdout
    assert gh_calls == ""
    assert git_args == ""


def test_renovate_manager_keeps_release_and_immutable_sha_in_sync() -> None:
    # Guard real-file extraction and demonstrate the pair's intended update
    # shape; Renovate's replacement engine is not exercised by this test.
    renovate = json.loads((REPO_ROOT / "renovate.json").read_text())
    manager = renovate["customManagers"][0]
    pattern = manager["matchStrings"][0].replace("(?<", "(?P<")
    workflow = (REPO_ROOT / ".github/workflows/pypi.yml").read_text()
    match = re.search(pattern, workflow)

    assert match is not None
    assert manager["datasourceTemplate"] == "github-tags"
    assert manager["versioningTemplate"] == "semver"
    assert renovate["autoReplaceGlobalMatch"] is True
    assert match.group("packageName") == REPO
    current_value = match.group("currentValue")
    assert re.fullmatch(r"v\d+\.\d+\.\d+", current_value)
    tag_record = re.search(
        r"^\s*# publisher-module-sha: (v\d+\.\d+\.\d+) [0-9a-f]{40}$",
        workflow,
        re.MULTILINE,
    )
    assert tag_record is not None
    assert current_value == tag_record.group(1)
    current_digest = match.group("currentDigest")
    active_ref = re.search(
        rf"github\.com/{re.escape(REPO)}@([0-9a-f]{{40}})",
        workflow,
    )
    assert active_ref is not None
    assert current_digest == active_ref.group(1)

    next_version = "v99.0.0"
    upgraded = match.group(0).replace(current_value, next_version)
    upgraded = upgraded.replace(current_digest, RETAGGED_SHA)
    assert upgraded.count(RETAGGED_SHA) == 2
    assert f"@{next_version}" not in upgraded  # the build reference remains a SHA
    assert f"publisher-module-sha: {next_version} {RETAGGED_SHA}" in upgraded


def test_newer_stable_release_uses_semver_order_and_opens_issue(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        pin="v0.9.0",
        refs=(
            f"{PINNED_SHA} refs/tags/v0.9.0",
            f"{PINNED_SHA} refs/tags/v0.10.0-rc.1",
            f"{PINNED_SHA} refs/tags/v0.10.0.1",
            f"{PINNED_SHA} refs/tags/v0.10.0",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "latest_release=v0.10.0" in result.stdout
    assert "latest upstream release is v0.10.0" in gh_calls
    assert f"issue create -R {ISSUE_REPO}" in gh_calls


def test_existing_stale_ref_issue_is_not_duplicated(tmp_path: Path) -> None:
    result, gh_calls, _, gh_queries = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body=(
            f"pypi.yml pins {REPO}@v0.2.2, but the latest upstream release is "
            f"v0.2.3. Review and re-pin. See #49. {ISSUE_MARKER}\n"
            "Operator note that must survive an automation refresh."
        ),
    )

    assert result.returncode == 0, result.stderr
    assert gh_calls == ""
    assert "select(.title == env.DAGGER_REF_DRIFT_ISSUE_TITLE)" in gh_queries


@requires_jq
def test_issue_list_jq_tsv_round_trips_tabs_and_newlines(tmp_path: Path) -> None:
    existing_body = f"Old alert with a tab\there and a newline\nhere. {ISSUE_MARKER}"
    result, gh_calls, _, gh_queries = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body=existing_body,
        run_real_jq=True,
    )

    assert result.returncode == 0, result.stderr
    assert "issue edit 123" in gh_calls
    assert "issue create" not in gh_calls
    assert "@base64" in gh_queries
    assert "@tsv" in gh_queries


@requires_jq
def test_real_jq_ignores_other_titles_before_creating_alert(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        run_real_jq=True,
        other_issues=(("456", "An unrelated issue", "not the stale-ref alert"),),
    )

    assert result.returncode == 0, result.stderr
    assert f"issue create -R {ISSUE_REPO}" in gh_calls


@requires_jq
def test_real_jq_empty_body_suppresses_duplicate_alert(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body="",
        run_real_jq=True,
    )

    assert result.returncode != 0
    assert "issue create" not in gh_calls
    assert "open same-title issue #123 is unmarked" in result.stderr


@requires_jq
def test_real_jq_recovery_closes_marked_alert(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        pin="v0.2.3",
        open_issues="1",
        open_issue_body=f"old stale-ref alert {ISSUE_MARKER}",
        run_real_jq=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"issue close 123 --repo {ISSUE_REPO}" in gh_calls


def test_workflow_pin_parses_without_final_newline(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, final_newline=False)

    assert result.returncode == 0, result.stderr
    assert "pinned=v0.2.2 latest_release=v0.2.2" in result.stdout
    assert gh_calls == ""


def test_existing_issue_body_is_refreshed_when_latest_release_changes(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.4",
        ),
        open_issues="1",
        open_issue_body=(
            f"pypi.yml pins {REPO}@v0.2.2, but the latest upstream release is "
            f"v0.2.3. Review and re-pin. See #49. {ISSUE_MARKER}\n"
            "Operator note that must survive an automation refresh."
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "issue edit 123" in gh_calls
    assert "latest upstream release is v0.2.4" in gh_calls
    assert f"--repo {ISSUE_REPO}" in gh_calls
    assert "Operator note that must survive an automation refresh." in gh_calls
    assert "issue create" not in gh_calls


def test_recovered_pin_closes_existing_stale_issue(tmp_path: Path) -> None:
    result, gh_calls, _, gh_queries = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        pin="v0.2.3",
        open_issues="1",
        open_issue_body=f"old stale-ref alert {ISSUE_MARKER}",
    )

    assert result.returncode == 0, result.stderr
    assert "issue close 123" in gh_calls
    assert "publisher pin is current at v0.2.3" in gh_calls
    assert f"--repo {ISSUE_REPO}" in gh_calls
    assert "issue edit" not in gh_calls
    assert "issue create" not in gh_calls
    assert "select(.title == env.DAGGER_REF_DRIFT_ISSUE_TITLE)" in gh_queries


def test_recovery_closes_all_marked_duplicate_issues(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        pin="v0.2.3",
        open_issues="1",
        open_issue_body=f"old stale-ref alert {ISSUE_MARKER}",
        marked_issue_number="123\n124",
    )

    assert result.returncode == 0, result.stderr
    assert "issue close 123" in gh_calls
    assert "issue close 124" in gh_calls


def test_recovery_leaves_unmarked_same_title_issue_untouched(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        pin="v0.2.3",
        open_issues="1",
        open_issue_body="A manually maintained issue with the same title.",
    )

    assert result.returncode == 0, result.stderr
    assert gh_calls == ""


def test_stale_check_updates_marked_issue_when_manual_issue_is_first(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body="A manually maintained issue with the same title.",
        open_issue_number="124",
        marked_issue_number="123",
        marked_issue_body=f"old stale-ref alert {ISSUE_MARKER}",
    )

    assert result.returncode == 0, result.stderr
    # The body marker must identify automation's issue even if an unmarked
    # same-title issue sorts first in GitHub's result list.
    assert "issue edit 123" in gh_calls
    assert "issue create" not in gh_calls


def test_stale_check_updates_all_marked_duplicate_issues(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body=f"old stale-ref alert {ISSUE_MARKER}",
        marked_issue_number="123\n124",
    )

    assert result.returncode == 0, result.stderr
    assert "issue edit 123" in gh_calls
    assert "issue edit 124" in gh_calls
    assert "issue create" not in gh_calls


def test_stale_check_does_not_duplicate_unmarked_same_title_issue(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body="A manually maintained issue with the same title.",
    )

    assert result.returncode != 0
    assert gh_calls == ""
    assert "ERROR: stale publisher alert suppressed" in result.stderr
    assert "open same-title issue #123 is unmarked" in result.stderr
    assert f"https://github.com/{ISSUE_REPO}/issues/123" in result.stderr
    assert f"add '{ISSUE_MARKER}' to its body or close it" in result.stderr


def test_appended_first_line_operator_note_survives_issue_refresh(
    tmp_path: Path,
) -> None:
    existing_body = (
        f"old stale-ref alert {ISSUE_MARKER} operator note without a newline"
    )
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body=existing_body,
    )

    assert result.returncode == 0, result.stderr
    assert "issue edit 123" in gh_calls
    assert "operator note without a newline" in gh_calls


def test_legacy_rwx_issue_is_migrated_when_stale(tmp_path: Path) -> None:
    legacy_body = (
        f"pypi.yml pins {REPO}@{'d' * 40}, but upstream HEAD is "
        f"{'e' * 40}. Review and re-pin. See #45.\r\n"
        "Additional operator context."
    )
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        open_issues="1",
        open_issue_body=legacy_body,
    )

    assert result.returncode == 0, result.stderr
    assert "issue edit 123" in gh_calls
    assert ISSUE_MARKER in gh_calls
    assert "Additional operator context." in gh_calls
    assert "issue create" not in gh_calls


def test_recovery_closes_legacy_rwx_issue(tmp_path: Path) -> None:
    legacy_body = (
        f"pypi.yml pins {REPO}@{'d' * 40}, but upstream HEAD is "
        f"{'e' * 40}. Review and re-pin. See #45."
    )
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        pin="v0.2.3",
        open_issues="1",
        open_issue_body=legacy_body,
    )

    assert result.returncode == 0, result.stderr
    assert "issue close 123" in gh_calls


def test_issue_lookup_failure_does_not_create_duplicate_issue(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.2",
            f"{PINNED_SHA} refs/tags/v0.2.3",
        ),
        gh_issue_list_exit_code=1,
    )

    assert result.returncode != 0
    assert "mock issue lookup failure" in result.stderr
    assert "issue create" not in gh_calls


@pytest.mark.parametrize("pin", ["main", "deadbeef", "v0.2.2-rc.1", "v01.2.3"])
def test_unrecognized_pin_fails_clearly(tmp_path: Path, pin: str) -> None:
    result, _, _, _ = _run_check(tmp_path, pin=pin)

    assert result.returncode != 0
    assert "invalid release tag" in result.stderr


@pytest.mark.parametrize("active_sha", ["v0.2.2", "1cdcc45", "A" * 40])
def test_active_module_ref_rejects_non_sha_values(
    tmp_path: Path,
    active_sha: str,
) -> None:
    result, _, _, _ = _run_check(tmp_path, active_sha=active_sha)

    assert result.returncode != 0
    assert "expected exactly one SHA-pinned" in result.stderr


def test_multiple_pins_fail_clearly(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, second_pin="v0.2.3")

    assert result.returncode != 0
    assert "found 2" in result.stderr
    assert gh_calls == ""


def test_missing_pinned_tag_fails_instead_of_suggesting_downgrade(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        pin="v0.2.3",
        refs=(f"{PINNED_SHA} refs/tags/v0.2.2",),
    )

    assert result.returncode != 0
    assert "that stable release tag does not exist upstream" in result.stderr
    assert gh_calls == ""


def test_no_stable_tags_fails_clearly(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(
            f"{PINNED_SHA} refs/tags/v0.2.3-rc.1",
            f"{PINNED_SHA} refs/tags/v0.2.3.1",
        ),
    )

    assert result.returncode != 0
    assert "no stable vMAJOR.MINOR.PATCH release tags found" in result.stderr
    assert gh_calls == ""


def test_upstream_tag_lookup_failure_has_a_clear_diagnostic(
    tmp_path: Path,
) -> None:
    result, gh_calls, _, _ = _run_check(tmp_path, git_exit_code=2)

    assert result.returncode != 0
    assert "failed to list release tags from" in result.stderr
    assert gh_calls == ""


def test_tag_retarget_is_detected_against_recorded_sha(tmp_path: Path) -> None:
    result, gh_calls, _, _ = _run_check(
        tmp_path,
        refs=(f"{RETAGGED_SHA} refs/tags/v0.2.2",),
    )

    assert result.returncode != 0
    assert f"resolves to {RETAGGED_SHA}" in result.stderr
    assert f"records {PINNED_SHA}" in result.stderr
    assert gh_calls == ""


@pytest.mark.parametrize(
    "extra_args",
    [("--verify",), ("--verify-tag",), ("--verify-pin", "extra")],
)
def test_invalid_arguments_fail_before_network_access(
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> None:
    result, gh_calls, git_args, gh_queries = _run_check(
        tmp_path,
        extra_args=extra_args,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert gh_calls == ""
    assert git_args == ""
    assert gh_queries == ""


def test_missing_github_token_fails_before_network_lookup(tmp_path: Path) -> None:
    result, gh_calls, git_args, gh_queries = _run_check(
        tmp_path,
        git_exit_code=2,
        gh_token=None,
    )

    assert result.returncode != 0
    assert "GITHUB_TOKEN vault secret not provisioned" in result.stderr
    assert git_args == ""
    assert gh_calls == ""
    assert gh_queries == ""
