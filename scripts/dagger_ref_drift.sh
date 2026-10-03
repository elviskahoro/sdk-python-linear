#!/usr/bin/env bash
set -euo pipefail

repo=elviskahoro/sdk-python-publish-to-pypi
issue_repo=elviskahoro/sdk-python-linear
issue_number=49
issue_marker="<!-- sdk-python-linear:dagger-publisher-drift -->"
title="Dagger publish module ref is stale"
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
issue_body_with_operator_notes() {
  local current_body=$1
  local previous_body=$2
  if [[ ${previous_body} == *"${issue_marker}"* ]]; then
    local operator_notes=${previous_body#*"${issue_marker}"}
    if [[ -n ${operator_notes//[[:space:]]/} ]]; then
      printf '%s%s' "${current_body}" "${operator_notes}"
      return
    fi
  elif [[ ${previous_body} == *$'\n'* ]]; then
    local operator_notes=${previous_body#*$'\n'}
    if [[ -n ${operator_notes} ]]; then
      printf '%s\n%s' "${current_body}" "${operator_notes}"
      return
    fi
  fi
  printf '%s' "${current_body}"
}
open_issues=$(list_open_same_title_issues)
if [[ ${pinned} == "${latest}" ]]; then
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
    updated_body=$(issue_body_with_operator_notes "${body}" "${issue_body}")
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
