#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${ROOT_DIR}/fnos-native/cinelink"
BUILD_ROOT="${ROOT_DIR}/dist/fnos-native"
PACKAGE_DIR="${BUILD_ROOT}/cinelink"
ALIST_VERSION="${ALIST_VERSION:-v3.60.0}"

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR" "$BUILD_ROOT"

rsync -a "$PROJECT_DIR/" "$PACKAGE_DIR/"
rsync -a "${ROOT_DIR}/fnos/cinelink/ICON.PNG" "$PACKAGE_DIR/ICON.PNG"
rsync -a "${ROOT_DIR}/fnos/cinelink/ICON_256.PNG" "$PACKAGE_DIR/ICON_256.PNG"
mkdir -p "${PACKAGE_DIR}/app/ui/images"
rsync -a "${ROOT_DIR}/fnos/cinelink/app/ui/images/" "${PACKAGE_DIR}/app/ui/images/"

mkdir -p "${PACKAGE_DIR}/app/cinelink" "${PACKAGE_DIR}/app/bin" "${PACKAGE_DIR}/app/site-packages"

rsync -a "${ROOT_DIR}/" "${PACKAGE_DIR}/app/cinelink/" \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'env/' \
  --exclude 'data/' \
  --exclude 'bin/' \
  --exclude 'dist/' \
  --exclude 'fnos/' \
  --exclude 'fnos-native/' \
  --exclude 'third_party/' \
  --exclude '*.fpk' \
  --exclude '*.log' \
  --exclude '*.db' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3'

echo "Downloading AList ${ALIST_VERSION}..."
curl -fsSL -o "${BUILD_ROOT}/alist.tar.gz" \
  "https://github.com/AlistGo/alist/releases/download/${ALIST_VERSION}/alist-linux-amd64.tar.gz"
tar -xzf "${BUILD_ROOT}/alist.tar.gz" -C "${PACKAGE_DIR}/app/bin" alist
chmod +x "${PACKAGE_DIR}/app/bin/alist"
rm -f "${BUILD_ROOT}/alist.tar.gz"

echo "Resolving portable Python runtime..."
PYTHON_URL="$(python3 - <<'PY'
import json
import sys
import urllib.request

url = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
with urllib.request.urlopen(url, timeout=30) as response:
    release = json.load(response)

assets = [
    asset for asset in release.get("assets", [])
    if "cpython-3.12" in asset.get("name", "")
    and "x86_64-unknown-linux-gnu-install_only_stripped" in asset.get("name", "")
    and asset.get("name", "").endswith(".tar.gz")
]
if not assets:
    print("No matching python-build-standalone asset found", file=sys.stderr)
    sys.exit(1)
assets.sort(key=lambda item: item["name"], reverse=True)
print(assets[0]["browser_download_url"])
PY
)"

echo "Downloading Python runtime..."
curl -fsSL -o "${BUILD_ROOT}/python.tar.gz" "$PYTHON_URL"
tar -xzf "${BUILD_ROOT}/python.tar.gz" -C "${PACKAGE_DIR}/app"
rm -f "${BUILD_ROOT}/python.tar.gz"

PYTHON_BIN="$(find "${PACKAGE_DIR}/app" -path '*/bin/python3*' -type f | head -n 1)"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Portable Python binary not found" >&2
  find "${PACKAGE_DIR}/app" -maxdepth 5 -type f -path '*/bin/*' | sort >&2 || true
  exit 1
fi

PYTHON_ROOT="$(cd "$(dirname "$PYTHON_BIN")/.." && pwd)"
TARGET_PYTHON_ROOT="$(cd "${PACKAGE_DIR}/app" && pwd)/python"
if [ "$PYTHON_ROOT" != "$TARGET_PYTHON_ROOT" ]; then
  rm -rf "${PACKAGE_DIR}/app/python-normalized"
  rsync -a "${PYTHON_ROOT}/" "${PACKAGE_DIR}/app/python-normalized/"
  rm -rf "${PACKAGE_DIR}/app/python"
  mv "${PACKAGE_DIR}/app/python-normalized" "${PACKAGE_DIR}/app/python"
fi

PYTHON_BIN="${PACKAGE_DIR}/app/python/bin/$(basename "$PYTHON_BIN")"
if [ ! -e "${PACKAGE_DIR}/app/python/bin/python3" ]; then
  ln -s "$(basename "$PYTHON_BIN")" "${PACKAGE_DIR}/app/python/bin/python3"
fi
PYTHON_BIN="${PACKAGE_DIR}/app/python/bin/python3"

echo "Installing Python dependencies..."
"$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "${ROOT_DIR}/requirements.txt" --target "${PACKAGE_DIR}/app/site-packages"

find "$PACKAGE_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${PACKAGE_DIR}/cmd" -type f -exec chmod +x {} +

echo "Native package workspace prepared at ${PACKAGE_DIR}"
