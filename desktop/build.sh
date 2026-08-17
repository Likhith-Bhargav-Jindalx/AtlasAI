#!/usr/bin/env bash
set -euo pipefail

# Builds the desktop exe as a fully self-contained bundle — no dependency
# on the machine running it already having Chromium installed.
#
# Playwright's own PyInstaller hook (playwright/_impl/__pyinstaller) bundles
# whatever's under the installed `playwright` package via collect_data_files,
# which is why PLAYWRIGHT_BROWSERS_PATH=0 (vendor the browser inside the
# package instead of the OS-level cache) is enough to get it collected in
# principle — but PyInstaller's own binary-processing/codesigning step
# chokes on the nested macOS .app bundle structure inside a vendored
# Chromium build if it's present *during* the build. The .local-browsers
# directory is moved aside before the build and copied back into the
# frozen bundle's playwright package directory afterward, bypassing that
# binary-processing step entirely — a plain file copy, not something
# PyInstaller's Analysis/EXE/COLLECT pipeline needs to touch.
#
# Run from the repo root: ./desktop/build.sh

cd "$(dirname "$0")/.."

VENV=engine/.venv
PLAYWRIGHT_PKG_DIR="$VENV/lib/"*"/site-packages/playwright/driver/package"
LOCAL_BROWSERS_STASH="$(mktemp -d)/local-browsers"
PRODUCTION_ENV=desktop/production.env

if [ ! -f "$PRODUCTION_ENV" ]; then
  echo "Missing $PRODUCTION_ENV — this exe connects directly to production" >&2
  echo "Postgres and Blob Storage, so it can't be built without real credentials" >&2
  echo "baked in. Copy desktop/production.env.example to $PRODUCTION_ENV and fill" >&2
  echo "in the real values, then re-run this script." >&2
  exit 1
fi

echo "Building frontend..."
(cd frontend && npm ci --silent && npm run build)

echo "Ensuring Chromium is vendored inside the playwright package (not the OS-level cache)..."
PLAYWRIGHT_BROWSERS_PATH=0 "$VENV/bin/python" -m playwright install chromium

# Glob expansion for PLAYWRIGHT_PKG_DIR happens here, once, now that the
# path definitely exists.
playwright_pkg_dir=$(compgen -G "$PLAYWRIGHT_PKG_DIR" | head -n1)
local_browsers="$playwright_pkg_dir/.local-browsers"

echo "Temporarily moving the vendored browser aside for a clean PyInstaller build..."
mv "$local_browsers" "$LOCAL_BROWSERS_STASH"

echo "Building with PyInstaller..."
rm -rf build dist atlas-desktop.spec
"$VENV/bin/pyinstaller" --onedir --windowed --name atlas-desktop \
  --add-data "frontend/dist:frontend_dist" --add-data "$PRODUCTION_ENV:." \
  --paths engine/src desktop/app.py

echo "Copying the vendored browser into the frozen bundle..."
cp -R "$LOCAL_BROWSERS_STASH" "dist/atlas-desktop/_internal/playwright/driver/package/.local-browsers"

echo "Restoring the vendored browser in the dev venv (for unfrozen testing)..."
cp -R "$LOCAL_BROWSERS_STASH" "$local_browsers"

echo "Zipping dist/atlas-desktop for distribution..."
(cd dist && zip -qr atlas-desktop.zip atlas-desktop)

echo "Done: dist/atlas-desktop.zip"
