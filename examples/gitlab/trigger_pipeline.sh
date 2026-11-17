#!/usr/bin/env bash
set -euo pipefail
: "${GITLAB_PROJECT_ID:?Set numeric GitLab project ID}"
: "${GITLAB_TRIGGER_TOKEN:?Set pipeline trigger token}"
: "${GITLAB_REF:?Set mirrored branch name}"
args=(--form-string "token=$GITLAB_TRIGGER_TOKEN" --form-string "ref=$GITLAB_REF")
if [[ -n "${QA_GITHUB_SHA:-}" ]]; then
  args+=(--form-string "variables[QA_GITHUB_SHA]=$QA_GITHUB_SHA")
fi
if [[ -n "${COVERAGE_RUN_ID:-}" ]]; then
  args+=(--form-string "variables[COVERAGE_RUN_ID]=$COVERAGE_RUN_ID")
fi
curl --silent --show-error --fail-with-body "${args[@]}" \
  "${GITLAB_HOST:-https://gitlab.com}/api/v4/projects/$GITLAB_PROJECT_ID/trigger/pipeline"
