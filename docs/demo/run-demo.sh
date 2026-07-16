#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/oh-my-multica-demo.XXXXXX")"
DELAY="${DEMO_DELAY:-1}"

step() {
  echo
  echo "$ $*"
  sleep "$DELAY"
}

run() {
  step "$*"
  "$@"
  sleep "$DELAY"
}

export OMAC_ENGINE=mock
export OMAC_WORKSPACE_ID=mock-workspace
export MOCK_AUTO_COMPLETE=true
export MOCK_AUTO_COMPLETE_DELAY=0

cp "$ROOT_DIR/docs/demo/demo-manifest.yaml" "$DEMO_DIR/delivery-demo.yaml"
cd "$DEMO_DIR"

run omac --version
run omac init \
  --engine mock \
  --workspace mock-workspace \
  --planner alice \
  --orchestrator bob \
  --workers alice,bob \
  --reviewers charlie
run omac dag show delivery-demo.yaml

step "OMAC_MOCK_FAIL_KEYS=api omac dag run delivery-demo.yaml"
set +e
OMAC_MOCK_FAIL_KEYS=api omac dag run delivery-demo.yaml
result=$?
set -e
if [[ "$result" -ne 20 ]]; then
  echo "Expected exit 20, received $result" >&2
  exit 1
fi

run omac node retry delivery-demo.yaml api
run omac dag run delivery-demo.yaml
run omac dag status delivery-demo.yaml

echo
echo "Demo converged."
