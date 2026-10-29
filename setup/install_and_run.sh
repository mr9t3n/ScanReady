#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="launch"
OFFLINE_ONLY="false"
DRY_RUN="false"
for argument in "$@"; do
  case "$argument" in
    test) MODE="test" ;;
    evaluate) MODE="evaluate" ;;
    --check) MODE="check" ;;
    --offline-only) OFFLINE_ONLY="true" ;;
    --dry-run) DRY_RUN="true" ;;
    *) echo "ERROR: Unknown ScanReady option: $argument" >&2; exit 2 ;;
  esac
done

for required in app.py requirements-lock.txt scanready/__init__.py scanready/launch.py; do
  if [[ ! -f "$required" ]]; then
    echo "ERROR: ScanReady appears incomplete. Please extract the complete ScanReady folder before running it." >&2
    exit 2
  fi
done

if command -v sha256sum >/dev/null 2>&1; then
  LOCK_HASH="$(sha256sum requirements-lock.txt | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  LOCK_HASH="$(shasum -a 256 requirements-lock.txt | awk '{print $1}')"
else
  echo "ERROR: No SHA-256 utility is available." >&2
  exit 2
fi

SYSTEM="$(uname -s)"
MACHINE="$(uname -m)"
case "$SYSTEM:$MACHINE" in
  Darwin:arm64)
    PLATFORM="macos-arm64"
    RUNTIME_ARCHIVE="vendor/python/cpython-3.11.16+20260924-aarch64-apple-darwin-install_only.tar.gz"
    RUNTIME_SHA256="d718e3c5c6f4b225ed25f88bf65e4c5d314e0dea0d716ea50bc9d038630c502b"
    ;;
  Darwin:x86_64)
    PLATFORM="macos-x86_64"
    RUNTIME_ARCHIVE="vendor/python/cpython-3.11.16+20260924-x86_64-apple-darwin-install_only.tar.gz"
    RUNTIME_SHA256="a93ee2dd8f2dbddbd85a0c9f1739f98d6ead305cbf445918a2fe28deb77f2d4b"
    ;;
  Linux:x86_64)
    PLATFORM="linux-x86_64"
    RUNTIME_ARCHIVE="vendor/python/cpython-3.11.16+20260924-x86_64-unknown-linux-gnu-install_only.tar.gz"
    RUNTIME_SHA256="49a52eb189878431a36efd137e7cd08f6429e59dc049b9cfb030bf706eb33fd4"
    ;;
  *) echo "ERROR: Unsupported platform: $SYSTEM $MACHINE" >&2; exit 2 ;;
esac

WHEEL_DIR="vendor/wheels/$PLATFORM"
RUNTIME_SOURCE="system-python"
if [[ -f "$RUNTIME_ARCHIVE" && -d "$WHEEL_DIR" ]]; then
  RUNTIME_SOURCE="bundled-archive"
fi
READY_ID="$LOCK_HASH:$RUNTIME_SHA256"
RUNTIME_PY=".runtime/python/bin/python3"
if [[ ! -x "$RUNTIME_PY" && -x ".runtime/venv/bin/python" ]]; then
  RUNTIME_PY=".runtime/venv/bin/python"
fi
RUNTIME_READY="false"
if [[ -x "$RUNTIME_PY" && -f ".runtime/ready.txt" && "$(tr -d '\r\n' < .runtime/ready.txt)" == "$READY_ID" ]]; then
  RUNTIME_READY="true"
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "ScanReady setup plan"
  echo "mode=$MODE"
  echo "offline-only=$OFFLINE_ONLY"
  echo "platform=$PLATFORM"
  echo "runtime-source=$RUNTIME_SOURCE"
  echo "runtime-ready=$RUNTIME_READY"
  if [[ "$RUNTIME_READY" == "true" ]]; then
    echo "action=reuse-runtime"
  elif [[ "$RUNTIME_SOURCE" == "bundled-archive" ]]; then
    echo "action=extract-bundled-runtime"
  else
    echo "action=create-online-runtime"
  fi
  exit 0
fi

if [[ "$RUNTIME_READY" != "true" ]]; then
  if [[ "$OFFLINE_ONLY" == "true" && ! -d "$WHEEL_DIR" ]]; then
    echo "ERROR: The $PLATFORM offline wheel bundle is missing. Re-extract the complete ScanReady folder." >&2
    exit 3
  fi
  rm -rf -- "$ROOT/.runtime"
  if [[ "$RUNTIME_SOURCE" == "bundled-archive" ]]; then
    if command -v sha256sum >/dev/null 2>&1; then
      ACTUAL_RUNTIME_HASH="$(sha256sum "$RUNTIME_ARCHIVE" | awk '{print $1}')"
    else
      ACTUAL_RUNTIME_HASH="$(shasum -a 256 "$RUNTIME_ARCHIVE" | awk '{print $1}')"
    fi
    if [[ "$ACTUAL_RUNTIME_HASH" != "$RUNTIME_SHA256" ]]; then
      echo "ERROR: The bundled $PLATFORM Python archive failed its SHA-256 integrity check." >&2
      exit 3
    fi
    mkdir -p .runtime
    tar -xzf "$RUNTIME_ARCHIVE" -C .runtime
    RUNTIME_PY=".runtime/python/bin/python3"
  else
    if ! command -v python3 >/dev/null 2>&1; then
      echo "ERROR: No Python runtime is available. Re-extract the complete ScanReady release." >&2
      exit 3
    fi
    python3 -m venv .runtime/venv
    RUNTIME_PY=".runtime/venv/bin/python"
  fi
  if [[ ! -x "$RUNTIME_PY" ]]; then
    echo "ERROR: ScanReady could not create its private Python runtime." >&2
    exit 3
  fi
  if [[ "$RUNTIME_SOURCE" == "bundled-archive" || "$OFFLINE_ONLY" == "true" ]]; then
    "$RUNTIME_PY" -m pip install --disable-pip-version-check --no-index --find-links "$WHEEL_DIR" -r requirements-lock.txt
  else
    "$RUNTIME_PY" -m pip install --disable-pip-version-check -r requirements-lock.txt
  fi
  printf '%s\n' "$READY_ID" > .runtime/ready.txt
fi

case "$MODE" in
  test) exec "$RUNTIME_PY" -m pytest -q ;;
  evaluate) exec "$RUNTIME_PY" -m scanready evaluate --split heldout --check ;;
  check)
    "$RUNTIME_PY" -m scanready selftest
    exec "$RUNTIME_PY" -m scanready launch --no-browser --smoke
    ;;
  launch) exec "$RUNTIME_PY" -m scanready launch ;;
esac

