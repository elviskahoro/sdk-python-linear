#!/usr/bin/env bash
# shellcheck disable=SC2310
# SC2310 is disabled file-wide on purpose: the bump-PR helpers are invoked
# in condition contexts (if/elif/||) by design -- each reports its own
# failure as a WARNING on stderr and returns status so the caller can fall
# back to the issue alert, and that contract requires set -e to stay
# disabled inside those calls.
set -euo pipefail

repo=elviskahoro/sdk-python-publish-to-pypi
issue_repo=elviskahoro/sdk-python-linear
issue_number=49
issue_marker="<!-- sdk-python-linear:dagger-publisher-drift -->"
title="Dagger publish module ref is stale"
# The stale-pin alert is a ready-to-review bump PR from a fixed automation
# branch, deduplicated by head and base ref (GitHub allows one open PR per
# head:base pair, so the title is free to carry the version). The stale-ref
# issue remains the fallback alert for when the PR machinery cannot run --
# for example, while the vault token still lacks Contents or Pull-requests
# write scopes -- so drift is never silent.
pr_branch="automation/dagger-publisher-pin"
pr_base_branch="${DRIFT_BASE_BRANCH:-main}"
pr_marker="<!-- sdk-python-linear:dagger-publisher-bump -->"
pr_issue_number=48
# Keep the legacy matcher aligned with the previous RWX issue body.
legacy_body_prefix="pypi.yml pins ${repo}@"
legacy_body_head_phrase="upstream HEAD"
legacy_body_action_phrase="Review and re-pin"
pypi_workflow=.github/workflows/pypi.yml
verify_tag_only=0
if [[ $# -eq 1 && $1 == --verify-tag ]]; then
  verify_tag_only=1
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--verify-tag]" >&2
  exit 2
fi

# The weekly drift task needs GitHub access; the publish-time tag check does not.
if [[ ${verify_tag_only} -eq 0 ]]; then
  : "${GH_TOKEN:?GITHUB_TOKEN vault secret not provisioned -- rwx vaults secrets set --vault sdk-python-linear GITHUB_TOKEN=...}"
fi

# Require exactly one publisher-module ref, then validate it as stable SemVer.
# Bash parsing avoids GNU-only grep extensions and fails closed if the workflow
# changes format instead of silently disabling the weekly release check.
pin_pattern="github\\.com/${repo}@([^[:space:]]+)"
pins=()
while IFS= read -r line || [[ -n ${line} ]]; do
  [[ ${line} =~ ^[[:space:]]*# ]] && continue
  if [[ ${line} =~ ${pin_pattern} ]]; then
    pins+=("${BASH_REMATCH[1]}")
  fi
done <"${pypi_workflow}"
pin_count=${#pins[@]}
if [[ ${pin_count} -ne 1 ]]; then
  echo "expected exactly one stable version-tagged ${repo} ref in ${pypi_workflow}; found ${pin_count} (expected @vMAJOR.MINOR.PATCH)" >&2
  exit 1
fi
pinned=${pins[0]}
if [[ ! ${pinned} =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "expected exactly one stable version-tagged ${repo} ref in ${pypi_workflow}; found @${pinned} (expected @vMAJOR.MINOR.PATCH)" >&2
  exit 1
fi

# Keep the human-readable release pin while checking its resolved commit SHA
# independently before the build and during the weekly drift check.
expected_shas=$(awk -v version="${pinned}" '$1 == "#" && $2 == "publisher-module-sha:" && $3 == version && NF == 4 { print $4 }' "${pypi_workflow}")
sha_count=$(printf '%s\n' "${expected_shas}" | awk 'NF { count++ } END { print count+0 }')
if [[ ${sha_count} -ne 1 ]]; then
  echo "expected exactly one publisher-module-sha record for ${pinned} in ${pypi_workflow}; found ${sha_count}" >&2
  exit 1
fi
expected_sha=${expected_shas}
if [[ ! ${expected_sha} =~ ^[0-9a-f]{40}$ ]]; then
  echo "publisher-module-sha for ${pinned} in ${pypi_workflow} must be a 40-character lowercase SHA" >&2
  exit 1
fi

if ! remote_tags=$(git ls-remote --tags "https://github.com/${repo}" 'refs/tags/v*'); then
  echo "failed to list release tags from ${repo}" >&2
  exit 1
fi
stable_tags=$(printf '%s\n' "${remote_tags}" |
  awk '$2 ~ /^refs\/tags\/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/ { sub("refs/tags/v", "", $2); print $2 }' |
  sort -t. -k1,1n -k2,2n -k3,3n |
  sed 's/^/v/')
if [[ -z ${stable_tags} ]]; then
  echo "no stable vMAJOR.MINOR.PATCH release tags found in ${repo}" >&2
  exit 1
fi
if ! grep -Fxq "${pinned}" <<<"${stable_tags}"; then
  echo "configured publisher ref ${repo}@${pinned}, but that stable release tag does not exist upstream" >&2
  exit 1
fi
resolved_sha=$(awk -v ref="refs/tags/${pinned}" '$2 == ref { direct = $1 } $2 == ref "^{}" { peeled = $1 } END { if (peeled != "") print peeled; else print direct }' <<<"${remote_tags}")
if [[ -z ${resolved_sha} ]]; then
  echo "could not resolve ${repo}@${pinned} to a commit SHA" >&2
  exit 1
fi
if [[ ${resolved_sha} != "${expected_sha}" ]]; then
  echo "${repo}@${pinned} resolves to ${resolved_sha}, but pypi.yml records ${expected_sha}" >&2
  exit 1
fi

latest=$(tail -n1 <<<"${stable_tags}")
echo "pinned=${pinned} latest_release=${latest}"

# The PyPI workflow calls this mode immediately before invoking Dagger.
if [[ ${verify_tag_only} -eq 1 ]]; then
  echo "checked ${repo}@${pinned} commit_sha=${resolved_sha}"
  exit 0
fi

# Resolve the latest release's commit so a bump PR can write the new
# publisher-module-sha record; the publish-time check above then validates
# exactly the pair this automation pinned.
latest_sha=$(awk -v ref="refs/tags/${latest}" '$2 == ref { direct = $1 } $2 == ref "^{}" { peeled = $1 } END { if (peeled != "") print peeled; else print direct }' <<<"${remote_tags}")
if [[ -z ${latest_sha} || ! ${latest_sha} =~ ^[0-9a-f]{40}$ ]]; then
  echo "could not resolve ${repo}@${latest} to a commit SHA" >&2
  exit 1
fi

# The pin is known to exist in stable_tags, and latest is its semantic maximum.
list_open_same_title_issues() {
  # Filter the fetched open issue set locally: GitHub's search index can lag
  # after an issue is created, which could otherwise cause a duplicate alert.
  DAGGER_REF_DRIFT_ISSUE_TITLE="${title}" gh issue list -R "${issue_repo}" \
    --state open --limit 1000 --json number,title,body \
    --jq '.[] | select(.title == env.DAGGER_REF_DRIFT_ISSUE_TITLE) | [.number, ((.body // "") | @base64)] | @tsv'
}
decode_base64() {
  if base64 --decode </dev/null >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}
is_legacy_automation_issue() {
  local body=${1//$'\r'/}
  [[ ${body} == *"${legacy_body_prefix}"* &&
    ${body} == *"${legacy_body_head_phrase}"* &&
    ${body} == *"${legacy_body_action_phrase}"* ]]
}
is_automation_issue() {
  local body=$1
  [[ ${body} == *"${issue_marker}"* ]] || is_legacy_automation_issue "${body}"
}
# Rebuild an automation-owned alert body while preserving any operator notes
# appended after the marker. The newline fallback only applies to the issue
# marker: pre-marker legacy issues carried notes after the first line, while
# bump PRs have been marker-owned from the start.
body_with_operator_notes() {
  local current_body=$1
  local previous_body=$2
  local marker=${3:-${issue_marker}}
  if [[ ${previous_body} == *"${marker}"* ]]; then
    local operator_notes=${previous_body#*"${marker}"}
    if [[ -n ${operator_notes//[[:space:]]/} ]]; then
      printf '%s%s' "${current_body}" "${operator_notes}"
      return
    fi
  elif [[ ${marker} == "${issue_marker}" && ${previous_body} == *$'\n'* ]]; then
    local operator_notes=${previous_body#*$'\n'}
    if [[ -n ${operator_notes} ]]; then
      printf '%s\n%s' "${current_body}" "${operator_notes}"
      return
    fi
  fi
  printf '%s' "${current_body}"
}

# Rewrite a workflow's publisher pin: the run-step module ref and the
# publisher-module-sha record move together to <latest> <latest_sha>. Reads
# base64 workflow content on stdin and writes the rewritten workflow's
# base64 on stdout. Fails closed unless the input carries exactly one ref
# and one matching sha record -- the same structure the publish-time parser
# enforces -- so a reformatted or diverging workflow stops the automation
# instead of being silently mis-bumped.
rewrite_pin_b64() {
  local counts
  decode_base64 >"${tmp_dir}/workflow.yml"
  rm -f "${tmp_dir}/counts"
  awk -v module="github.com/${repo}@" \
    -v new_tag="${latest}" \
    -v new_sha="${latest_sha}" \
    -v counts_file="${tmp_dir}/counts" '
    { lines[NR] = $0 }
    END {
      # Pass 1: the run-step module ref, outside comments.
      ref_matches = 0
      ref_line = 0
      ref_tag = ""
      for (i = 1; i <= NR; i++) {
        line = lines[i]
        if (line ~ /^[ \t]*#/) continue
        pos = index(line, module)
        if (pos == 0) continue
        value = substr(line, pos + length(module))
        sub(/[ \t].*$/, "", value)
        if (value !~ /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/) continue
        ref_matches++
        if (ref_matches == 1) { ref_line = i; ref_tag = value }
      }
      # Pass 2: the publisher-module-sha record naming that ref.
      record_matches = 0
      record_line = 0
      for (i = 1; i <= NR; i++) {
        line = lines[i]
        if (line !~ /^[ \t]*#/) continue
        fields = split(line, f)
        if (fields != 4) continue
        if (f[1] != "#" || f[2] != "publisher-module-sha:") continue
        if (f[3] != ref_tag || f[4] !~ /^[0-9a-f]{40}$/) continue
        record_matches++
        if (record_matches == 1) record_line = i
      }
      # Pass 3: rewrite both halves in place, leaving every other byte alone.
      for (i = 1; i <= NR; i++) {
        line = lines[i]
        if (i == ref_line) {
          old = module ref_tag
          new = module new_tag
          pos = index(line, old)
          line = substr(line, 1, pos - 1) new substr(line, pos + length(old))
        } else if (i == record_line) {
          prefix = substr(line, 1, index(line, "#") - 1)
          line = prefix "# publisher-module-sha: " new_tag " " new_sha
        }
        print line
      }
      print ref_matches " " record_matches > counts_file
    }
  ' "${tmp_dir}/workflow.yml" >"${tmp_dir}/rewritten.yml" || return 1
  counts=$(cat "${tmp_dir}/counts" 2>/dev/null || printf '0 0')
  if [[ ${counts} != "1 1" ]]; then
    echo "expected one module ref plus one matching publisher-module-sha record in the fetched workflow; found ${counts}" >&2
    return 1
  fi
  base64 <"${tmp_dir}/rewritten.yml" | tr -d '\n'
}

# Fetch "<blob-sha> <base64-content>" for the pinned workflow as it exists
# on the given ref; one API call returns both fields the contents PUT needs.
fetch_workflow_on_ref() {
  gh api "repos/${issue_repo}/contents/${pypi_workflow}?ref=${1}" \
    --jq '.sha + " " + .content'
}

# Make sure the automation branch sits on the base branch's current head and
# carries this week's bumped workflow. Every mutation is a stateless
# GitHub API call: the weekly pipeline's clone has no .git directory
# (git/clone strips it), so there is no local git to commit through.
# Returns non-zero, with a WARNING on stderr, when any step fails so the
# caller can fall back to the issue alert.
ensure_bump_branch() {
  local base_head branch_head blob_and_content blob_sha current_b64 target_b64
  local branch_ref="repos/${issue_repo}/git/ref/heads/${pr_branch}"
  if ! base_head=$(gh api "repos/${issue_repo}/git/ref/heads/${pr_base_branch}" --jq .object.sha); then
    echo "WARNING: bump PR: could not resolve the ${pr_base_branch} head" >&2
    return 1
  fi
  if ! branch_head=$(gh api "${branch_ref}" --jq .object.sha 2>/dev/null); then
    if ! gh api -X POST "repos/${issue_repo}/git/refs" -f ref="refs/heads/${pr_branch}" -f sha="${base_head}" >/dev/null; then
      echo "WARNING: bump PR: could not create branch ${pr_branch} on ${pr_base_branch}" >&2
      return 1
    fi
    branch_head=${base_head}
  fi
  # Reset onto the current base head whenever the branch sits behind it. A
  # branch that already carries a bump commit is always behind (its tip is
  # the automation commit, not the base head), so an open PR is rebased
  # here every week -- otherwise merging it would revert base-branch
  # changes (for example Dependabot's action-SHA bumps in this very file)
  # that landed since the branch was last written.
  if [[ ${branch_head} != "${base_head}" ]]; then
    if ! gh api -X PATCH "repos/${issue_repo}/git/refs/heads/${pr_branch}" -F sha="${base_head}" -F force=true >/dev/null; then
      echo "WARNING: bump PR: could not reset ${pr_branch} onto the ${pr_base_branch} head" >&2
      return 1
    fi
  fi
  if ! blob_and_content=$(fetch_workflow_on_ref "${pr_branch}"); then
    echo "WARNING: bump PR: could not read ${pypi_workflow} on ${pr_branch}" >&2
    return 1
  fi
  blob_sha=${blob_and_content%% *}
  current_b64=$(printf '%s' "${blob_and_content#* }" | tr -d '\n')
  if ! target_b64=$(printf '%s' "${current_b64}" | rewrite_pin_b64); then
    echo "the pin in ${pypi_workflow} on ${pr_branch} did not parse as one ref plus one sha record; refusing to guess a bump" >&2
    return 1
  fi
  # The branch is at the base head and its workflow already equals this
  # week's target: nothing to commit, and skipping keeps an untouched
  # branch from accruing a no-op commit.
  if [[ ${target_b64} == "${current_b64}" ]]; then
    return 0
  fi
  if ! gh api -X PUT "repos/${issue_repo}/contents/${pypi_workflow}" \
    -f branch="${pr_branch}" \
    -f message="chore(deps): bump dagger publisher module to ${latest}" \
    -f content="${target_b64}" \
    -f sha="${blob_sha}" >/dev/null; then
    echo "WARNING: bump PR: could not commit the bumped ${pypi_workflow} to ${pr_branch}" >&2
    return 1
  fi
  return 0
}

# Open PRs from the automation branch targeting this run's base ref. One
# head branch can serve several bases (a dispatch against a non-main ref
# opens its own PR), so headRefName alone is not a unique key: filtering by
# base keeps the weekly main run from adopting -- and force-resetting the
# shared branch under -- a PR that targets some other base. The local filter
# and base64 transport mirror the issue list's defenses (search-index lag,
# tab-safe round trip).
list_open_bump_prs() {
  DAGGER_BUMP_PR_BRANCH="${pr_branch}" DAGGER_BUMP_PR_BASE="${pr_base_branch}" \
    gh pr list -R "${issue_repo}" \
    --state open --limit 1000 --json number,title,body,headRefName,baseRefName \
    --jq '.[] | select(.headRefName == env.DAGGER_BUMP_PR_BRANCH && .baseRefName == env.DAGGER_BUMP_PR_BASE) | [.number, .title, ((.body // "") | @base64)] | @tsv'
}

bump_pr_body() {
  printf 'The weekly drift check found %s pinned at %s (%s) while the latest upstream release is %s (%s). This PR moves both halves of the pin together: the run-step module ref and the publisher-module-sha record, so the publish-time --verify-tag check in pypi.yml validates the new pair as-is. Review and merge, or close to keep the pin at %s. See #%s. %s' \
    "${repo}" "${pinned}" "${expected_sha}" "${latest}" "${latest_sha}" \
    "${pinned}" "${pr_issue_number}" "${pr_marker}"
}

# Split a `gh ... --jq '[...] | @tsv'` row into tsv_field_1..3. Positional
# expansion is used instead of `read` IFS splitting because bash collapses
# runs of IFS whitespace (tabs), which would misplace an empty middle field.
split_tsv_row() {
  local row=$1
  local rest=${row#*$'\t'}
  tsv_field_1=${row%%$'\t'*}
  tsv_field_2=${rest%%$'\t'*}
  tsv_field_3=${rest#*$'\t'}
}

# Close automation-owned stale-ref issues once the bump PR carries the alert.
close_superseded_alert_issues() {
  local pr_reference=$1
  while IFS=$'\t' read -r open_issue issue_body_b64; do
    [[ -n ${open_issue} ]] || continue
    issue_body=$(printf '%s' "${issue_body_b64}" | decode_base64)
    issue_body=${issue_body//$'\r'/}
    if is_automation_issue "${issue_body}"; then
      gh issue close "${open_issue}" --repo "${issue_repo}" \
        --comment "Superseded by the automated bump PR${pr_reference}."
    fi
  done <<<"${open_issues}"
}

open_issues=$(list_open_same_title_issues)

# Scratch space for the workflow rewrite in the stale path below.
tmp_dir=$(mktemp -d)
trap 'rm -rf "${tmp_dir}"' EXIT

if [[ ${pinned} == "${latest}" ]]; then
  # Recovery: the pin is current again. Close whatever the automation left
  # open -- the bump PR and its branch, plus any fallback issue -- so a
  # stale alert never outlives its drift.
  if open_prs=$(list_open_bump_prs); then
    while IFS= read -r pr_row; do
      [[ -n ${pr_row} ]] || continue
      split_tsv_row "${pr_row}"
      pr_number=${tsv_field_1}
      pr_body_b64=${tsv_field_3}
      [[ ${pr_number} =~ ^[0-9]+$ ]] || continue
      pr_body=$(printf '%s' "${pr_body_b64}" | decode_base64)
      pr_body=${pr_body//$'\r'/}
      if [[ ${pr_body} == *"${pr_marker}"* ]]; then
        gh pr close "${pr_number}" --repo "${issue_repo}" \
          --comment "The publisher pin is current at ${pinned}; closing this stale bump PR."
        gh api -X DELETE "repos/${issue_repo}/git/refs/heads/${pr_branch}" >/dev/null 2>&1 ||
          echo "WARNING: could not delete ${pr_branch} after closing #${pr_number}" >&2
      fi
    done <<<"${open_prs}"
  else
    echo "WARNING: the pin is current but open bump PRs could not be listed; a stale bump PR may linger" >&2
  fi
  while IFS=$'\t' read -r open_issue issue_body_b64; do
    [[ -n ${open_issue} ]] || continue
    issue_body=$(printf '%s' "${issue_body_b64}" | decode_base64)
    issue_body=${issue_body//$'\r'/}
    if is_automation_issue "${issue_body}"; then
      gh issue close "${open_issue}" --repo "${issue_repo}" \
        --comment "The publisher pin is current at ${pinned}; closing this stale-ref alert."
    fi
  done <<<"${open_issues}"
  exit 0
fi

# Stale pin: the primary alert is a ready-to-review bump PR from the fixed
# automation branch; the issue alert at the bottom remains the fallback for
# when the PR machinery cannot run.
bump_body=$(bump_pr_body)
open_prs=$(list_open_bump_prs) ||
  echo "WARNING: open bump PRs could not be listed; attempting a fresh bump PR" >&2
pr_number=""
pr_title=""
pr_body_b64=""
while IFS= read -r pr_row; do
  [[ -n ${pr_row} ]] || continue
  split_tsv_row "${pr_row}"
  if [[ ${tsv_field_1} =~ ^[0-9]+$ ]]; then
    pr_number=${tsv_field_1}
    pr_title=${tsv_field_2}
    pr_body_b64=${tsv_field_3}
    break
  fi
done <<<"${open_prs}"

if [[ -n ${pr_number} ]]; then
  pr_body=$(printf '%s' "${pr_body_b64}" | decode_base64)
  pr_body=${pr_body//$'\r'/}
  # Refresh mode: the open PR already carries the alert, so failures here
  # surface loudly instead of falling back -- a second, duplicate alert
  # would add noise, not signal.
  if ! ensure_bump_branch; then
    echo "ERROR: the open bump PR #${pr_number} could not be refreshed" >&2
    exit 1
  fi
  if [[ ${pr_body} == *"${pr_marker}"* ]]; then
    new_title="Bump dagger publisher pin to ${latest}"
    new_body=$(body_with_operator_notes "${bump_body}" "${pr_body}" "${pr_marker}")
    if [[ ${pr_title} != "${new_title}" || ${pr_body} != "${new_body}" ]]; then
      gh pr edit "${pr_number}" --repo "${issue_repo}" --title "${new_title}" --body "${new_body}"
    fi
  else
    # The operator rewrote the PR body without the marker: their text wins.
    echo "open bump PR #${pr_number} carries no ${pr_marker}; leaving its body untouched" >&2
  fi
  close_superseded_alert_issues " (#${pr_number})"
  exit 0
fi

# Create mode: no open PR from the automation branch. Any failure below
# falls back to the issue alert.
if ! ensure_bump_branch; then
  echo "WARNING: bump PR automation incomplete; falling back to the stale-ref issue alert" >&2
elif pr_url=$(gh pr create -R "${issue_repo}" --head "${pr_branch}" --base "${pr_base_branch}" \
  --title "Bump dagger publisher pin to ${latest}" --body "${bump_body}"); then
  if [[ ${pr_url} =~ /pull/([0-9]+) ]]; then
    close_superseded_alert_issues " (#${BASH_REMATCH[1]})"
  else
    close_superseded_alert_issues ""
  fi
  exit 0
else
  echo "WARNING: the bump PR could not be opened; falling back to the stale-ref issue alert" >&2
fi

# Fallback alert: file or refresh the stale-ref issue. This is also the only
# path while the vault token lacks Contents/Pull-requests write scopes.
printf -v body 'pypi.yml pins %s@%s, but the latest upstream release is %s. Review and re-pin. See #%s. %s' \
  "${repo}" "${pinned}" "${latest}" "${issue_number}" "${issue_marker}"
has_automation_issue=0
suppressed_issue=
while IFS=$'\t' read -r open_issue issue_body_b64; do
  [[ -n ${open_issue} ]] || continue
  issue_body=$(printf '%s' "${issue_body_b64}" | decode_base64)
  issue_body=${issue_body//$'\r'/}
  if is_automation_issue "${issue_body}"; then
    has_automation_issue=1
    updated_body=$(body_with_operator_notes "${body}" "${issue_body}")
    if [[ ${issue_body} != "${updated_body}" ]]; then
      gh issue edit "${open_issue}" --repo "${issue_repo}" --body "${updated_body}"
    fi
  elif [[ -z ${suppressed_issue} ]]; then
    suppressed_issue=${open_issue}
  fi
done <<<"${open_issues}"
if [[ -z ${open_issues} ]]; then
  gh issue create -R "${issue_repo}" --title "${title}" \
    --body "${body}"
elif [[ ${has_automation_issue} -eq 0 ]]; then
  echo "ERROR: stale publisher alert suppressed: open same-title issue #${suppressed_issue} is unmarked (https://github.com/${issue_repo}/issues/${suppressed_issue}) and was left unchanged; add '${issue_marker}' to its body or close it to allow the automation alert" >&2
  exit 1
fi
