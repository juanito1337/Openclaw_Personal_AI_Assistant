#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
PYTHON=${OPENCLAW_DEV_PYTHON:-$ROOT/.venv/bin/python}

if [[ ! -x "$PYTHON" ]]; then
  echo "Testumgebung fehlt. Zuerst ./scripts/bootstrap-dev.sh ausfuehren." >&2
  exit 2
fi

mkdir -p "$ROOT/build"
PYTEST_LOCATION_ARGS=()
PYTEST_COMMAND=("$PYTHON" -m pytest)
PYTEST_COVERAGE_ARGS=(
  --cov=mail_agent
  --cov=personal_assistant
  --cov=docker
  --cov-branch
  --cov-report=term
  --cov-report=json:"$ROOT/build/coverage.json"
)
if [[ ${OPENCLAW_TEST_INSTALLED:-0} == "1" ]]; then
  cd "$ROOT/.."
  PYTEST_COMMAND=("$PYTHON" "$ROOT/scripts/run-installed-tests.py")
  PYTEST_COVERAGE_ARGS=()
  PYTEST_LOCATION_ARGS=(
    --rootdir="$ROOT"
    -c "$ROOT/pyproject.toml"
    --import-mode=importlib
    "$ROOT/tests"
  )
else
  cd "$ROOT"
fi
OPENCLAW_ENFORCE_TEST_BASELINE=1 \
PYTHONDONTWRITEBYTECODE=1 \
"${PYTEST_COMMAND[@]}" \
  "${PYTEST_COVERAGE_ARGS[@]}" \
  --junitxml="$ROOT/build/pytest.xml" \
  "${PYTEST_LOCATION_ARGS[@]}" \
  "$@"
