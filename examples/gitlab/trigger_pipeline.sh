#!/usr/bin/env bash
# ==============================================================================
# GitLab CI Pipeline Trigger Script
# Triggers a GitLab CI pipeline for Option B integration with GraphQL Coverage Proxy
# ==============================================================================
set -euo pipefail

GITLAB_HOST="${GITLAB_HOST:-https://gitlab.com}"
PROJECT_ID="${1:-${GITLAB_PROJECT_ID:-}}"
TRIGGER_TOKEN="${2:-${GITLAB_TRIGGER_TOKEN:-}}"
REF="${3:-main}"
COVERAGE_RUN_ID="${4:-$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "run-$(date +%s)")}"
COVERAGE_URL="${COVERAGE_URL:-http://coverage-proxy:8000}"
MIN_COVERAGE="${MIN_COVERAGE:-80.0}"

if [ -z "$PROJECT_ID" ] || [ -z "$TRIGGER_TOKEN" ]; then
  echo "Usage: $0 <PROJECT_ID> <TRIGGER_TOKEN> [REF] [COVERAGE_RUN_ID]"
  echo "Or set GITLAB_PROJECT_ID and GITLAB_TRIGGER_TOKEN environment variables."
  exit 1
fi

echo "Triggering GitLab CI pipeline..."
echo "  Project ID:      $PROJECT_ID"
echo "  Ref:             $REF"
echo "  Coverage Run ID: $COVERAGE_RUN_ID"
echo "  Coverage URL:    $COVERAGE_URL"
echo "  Min Coverage:    $MIN_COVERAGE%"

RESPONSE=$(curl -sS --fail-with-body -X POST \
  -F "token=$TRIGGER_TOKEN" \
  -F "ref=$REF" \
  -F "variables[COVERAGE_RUN_ID]=$COVERAGE_RUN_ID" \
  -F "variables[COVERAGE_URL]=$COVERAGE_URL" \
  -F "variables[COVERAGE_MIN_PERCENT]=$MIN_COVERAGE" \
  "$GITLAB_HOST/api/v4/projects/$PROJECT_ID/trigger/pipeline")

PIPELINE_ID=$(echo "$RESPONSE" | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
WEB_URL=$(echo "$RESPONSE" | grep -o '"web_url":"[^"]*' | head -1 | cut -d'"' -f4)

echo "Pipeline triggered successfully!"
echo "  Pipeline ID: $PIPELINE_ID"
echo "  Web URL:     $WEB_URL"
