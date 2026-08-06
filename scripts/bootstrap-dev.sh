#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${OPENCLAW_BOOTSTRAP_PYTHON:-python3}
VENV="$ROOT/.venv"
TOOLS_BIN="$ROOT/.tools/bin"
SHELLCHECK_VERSION=0.11.0
HADOLINT_VERSION=2.15.1

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "pip==26.2.1"
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/requirements-dev.lock"

mkdir -p "$TOOLS_BIN"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    shellcheck_arch=x86_64
    shellcheck_sha=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
    hadolint_arch=x86_64
    hadolint_sha=c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507
    ;;
  Linux-aarch64|Linux-arm64)
    shellcheck_arch=aarch64
    shellcheck_sha=12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588
    hadolint_arch=arm64
    hadolint_sha=f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d
    ;;
  *)
    echo "Nicht unterstuetzte Entwicklungsplattform: $(uname -s)-$(uname -m)" >&2
    exit 2
    ;;
esac

download_and_verify() {
  local url=$1
  local target=$2
  local expected_sha=$3
  local temporary
  temporary=$(mktemp)
  curl --fail --location --silent --show-error "$url" --output "$temporary"
  printf '%s  %s\n' "$expected_sha" "$temporary" | sha256sum --check --status
  install -m 0755 "$temporary" "$target"
  rm -f "$temporary"
}

if [[ ! -x "$TOOLS_BIN/shellcheck" ]] || [[ "$("$TOOLS_BIN/shellcheck" --version | awk '/^version:/ {print $2}')" != "$SHELLCHECK_VERSION" ]]; then
  archive=$(mktemp)
  directory=$(mktemp -d)
  trap 'rm -f "$archive"; rm -rf "$directory"' EXIT
  url="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.${shellcheck_arch}.tar.xz"
  curl --fail --location --silent --show-error "$url" --output "$archive"
  printf '%s  %s\n' "$shellcheck_sha" "$archive" | sha256sum --check --status
  tar -xJf "$archive" -C "$directory"
  install -m 0755 "$directory/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$TOOLS_BIN/shellcheck"
  rm -f "$archive"
  rm -rf "$directory"
  trap - EXIT
fi

if [[ ! -x "$TOOLS_BIN/hadolint" ]] || [[ "$("$TOOLS_BIN/hadolint" --version | awk '{print $4}')" != "$HADOLINT_VERSION" ]]; then
  download_and_verify \
    "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-Linux-${hadolint_arch}" \
    "$TOOLS_BIN/hadolint" \
    "$hadolint_sha"
fi

echo "Entwicklungswerkzeuge sind reproduzierbar eingerichtet."
