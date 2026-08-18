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

# --onedir --windowed on macOS produces TWO separate output trees: the flat
# dist/atlas-desktop/ folder above, AND a real dist/atlas-desktop.app bundle
# with its own independent Contents/Resources/playwright/... copy -- PyInstaller
# builds both from the same Analysis pass, but this reinsertion step (needed
# only because PyInstaller's own binary-processing chokes on the vendored
# browser's nested .app structure, per the top-of-file comment) has to be
# done manually into EACH tree. Missing this made the actual .app bundle --
# the one people double-click -- silently ship with no Chromium at all:
# playwright.chromium.launch() fails every single time, deterministically,
# for anyone running the .app rather than the bare executable inside the
# flat folder. Confirmed by inspecting a build made before this fix: the
# flat folder had .local-browsers, the .app did not.
if [ -d "dist/atlas-desktop.app" ]; then
  echo "Copying the vendored browser into the .app bundle (the flat folder's reinsertion above doesn't reach it)..."
  cp -R "$LOCAL_BROWSERS_STASH" "dist/atlas-desktop.app/Contents/Resources/playwright/driver/package/.local-browsers"
fi

echo "Restoring the vendored browser in the dev venv (for unfrozen testing)..."
cp -R "$LOCAL_BROWSERS_STASH" "$local_browsers"

# PyInstaller signs the .app bundle itself during BUNDLE-00.toc (see "Signing
# the BUNDLE..." in its own build output) -- but that happens BEFORE the
# vendored browser is added back in above, so it lands as an unsigned set of
# files inside an otherwise-signed bundle. Re-signing ad-hoc here (identity
# "-": no real certificate, just a local, unverified signature) covers
# everything now that it's all actually present, which is what lets
# Gatekeeper run this on a DIFFERENT machine than the one it was built on
# without every nested unsigned binary needing its own manual approval.
if [ -d "dist/atlas-desktop.app" ]; then
  echo "Re-signing dist/atlas-desktop.app (ad-hoc) now that the browser is actually in it..."
  codesign --force --deep --sign - "dist/atlas-desktop.app"
fi

echo "Zipping dist/atlas-desktop for distribution..."
(cd dist && zip -qr atlas-desktop.zip atlas-desktop)

if [ -d "dist/atlas-desktop.app" ]; then
  echo "Zipping dist/atlas-desktop.app for distribution -- this is the one to actually hand to someone else: a real macOS .app double-clicks and runs normally, unlike the bare executable inside atlas-desktop.zip's flat folder."
  (cd dist && zip -qry atlas-desktop-app.zip atlas-desktop.app)
fi

echo "Done: dist/atlas-desktop-app.zip (share this one) — dist/atlas-desktop.zip also produced for dev/testing"
