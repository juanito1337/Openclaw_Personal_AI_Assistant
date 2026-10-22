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
PYTEST_ENV=(OPENCLAW_ENFORCE_TEST_BASELINE=1 PYTHONDONTWRITEBYTECODE=1)
PYTEST_COVERAGE_ARGS=(
  --cov=mail_agent
  --cov=personal_assistant
  --cov=docker
  --cov-branch
  --cov-report=term
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
  # Bind this run to fresh data and a fresh JSON report, even with --cov-append
  # or user-supplied report options. Never validate a previous successful run.
  coverage_run_dir=$(mktemp -d "$ROOT/build/coverage-run.XXXXXX")
  trap 'rm -rf -- "$coverage_run_dir"' EXIT
  PYTEST_ENV+=("COVERAGE_FILE=$coverage_run_dir/.coverage")
  PYTEST_COVERAGE_ARGS+=("--cov-report=json:$coverage_run_dir/coverage.json")
fi
test_status=0
env "${PYTEST_ENV[@]}" \
"${PYTEST_COMMAND[@]}" \
  --junitxml="$ROOT/build/pytest.xml" \
  "${PYTEST_LOCATION_ARGS[@]}" \
  "$@" \
  "${PYTEST_COVERAGE_ARGS[@]}" || test_status=$?

# Collection completeness is enforced above, independently of pytest display
# arguments. In particular, -q must not disable the risk coverage gate.
if [[ ${OPENCLAW_TEST_INSTALLED:-0} != "1" ]]; then
  if [[ ! -s "$coverage_run_dir/coverage.json" ]]; then
    echo "Frischer Coveragebericht fehlt; vorhandene Berichte sind kein Nachweis fuer diesen Lauf." >&2
    if [[ $test_status != 0 ]]; then
      exit "$test_status"
    fi
    exit 1
  fi
  if [[ $test_status != 0 ]]; then
    mv -- "$coverage_run_dir/coverage.json" "$ROOT/build/coverage.json"
    exit "$test_status"
  fi
  # Decide on the private report before publishing the convenient shared path:
  # another concurrent run cannot substitute its coverage for this run's gate.
  "$PYTHON" "$ROOT/scripts/check-risk-coverage.py" "$coverage_run_dir/coverage.json" || test_status=$?
  mv -- "$coverage_run_dir/coverage.json" "$ROOT/build/coverage.json"
fi
exit "$test_status"
