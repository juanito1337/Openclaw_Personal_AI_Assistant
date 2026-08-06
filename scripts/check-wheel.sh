#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
PYTHON=${OPENCLAW_DEV_PYTHON:-$ROOT/.venv/bin/python}
OUTPUT="$ROOT/build/wheel"
TEMP_ROOT=$(mktemp -d)
SNAPSHOT="$TEMP_ROOT/source"
BUILD_SOURCE="$TEMP_ROOT/build-source"
FRESH_VENV="$TEMP_ROOT/venv"
trap 'rm -rf "$TEMP_ROOT"' EXIT

if [[ ! -x "$PYTHON" ]]; then
  echo "Testumgebung fehlt. Zuerst ./scripts/bootstrap-dev.sh ausfuehren." >&2
  exit 2
fi

mkdir -p "$SNAPSHOT" "$BUILD_SOURCE" "$OUTPUT"
cd "$ROOT"
while IFS= read -r -d '' source_file; do
  [[ -f "$source_file" ]] && printf '%s\0' "$source_file"
done < <(git ls-files --cached --others --exclude-standard -z) \
  | tar --null --files-from=- --create --file=- \
  | tar --extract --file=- --directory="$SNAPSHOT"

PYTHONDONTWRITEBYTECODE=1 OPENCLAW_WORKSPACE="$SNAPSHOT" \
  "$PYTHON" "$SNAPSHOT/scripts/source-manifest.py" verify >/dev/null
cp -a "$SNAPSHOT/." "$BUILD_SOURCE/"
git -C "$SNAPSHOT" init --quiet
git -C "$SNAPSHOT" add --all
rm -f "$OUTPUT"/*.whl
build_started=$(date +%s%N)
"$PYTHON" -m build --wheel --outdir "$OUTPUT" "$BUILD_SOURCE"
build_finished=$(date +%s%N)
wheel=$(find "$OUTPUT" -maxdepth 1 -type f -name '*.whl' -print -quit)
[[ -n "$wheel" ]]
"$PYTHON" "$SNAPSHOT/scripts/check_artifact.py" wheel "$wheel"

"$PYTHON" -m venv "$FRESH_VENV"
"$FRESH_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "pip==26.2.1"
"$FRESH_VENV/bin/python" -m pip install --disable-pip-version-check "$wheel"
"$FRESH_VENV/bin/python" -m pip install --disable-pip-version-check -r "$SNAPSHOT/requirements-dev.lock"
(
  cd "$TEMP_ROOT"
  "$FRESH_VENV/bin/personal-assistant" --help >/dev/null
  "$FRESH_VENV/bin/python" -c 'import mail_agent, personal_assistant; assert "site-packages" in mail_agent.__file__; assert "site-packages" in personal_assistant.__file__'
)
OPENCLAW_WORKSPACE="$SNAPSHOT" "$FRESH_VENV/bin/personal-assistant" version --verify >/dev/null
OPENCLAW_DEV_PYTHON="$FRESH_VENV/bin/python" OPENCLAW_TEST_INSTALLED=1 \
  OPENCLAW_WORKSPACE="$SNAPSHOT" \
  "$SNAPSHOT/scripts/run-tests.sh" -q

wheel_bytes=$(stat -c %s "$wheel")
build_ms=$(((build_finished - build_started) / 1000000))
printf '{"wheel":"%s","bytes":%s,"build_ms":%s}\n' \
  "$(basename "$wheel")" "$wheel_bytes" "$build_ms" > "$ROOT/build/wheel-baseline.json"
echo "Wheel-Pruefung erfolgreich: $(basename "$wheel"), ${wheel_bytes} Bytes, Build ${build_ms} ms"
