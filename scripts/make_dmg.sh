#!/usr/bin/env bash
# Build the pxlsync macOS installer on a Mac.
#
#   scripts/make_dmg.sh
#
# Output: dist/pxlsync-<version>.dmg  (open it, drag pxlsync into Applications)
#
# Needs: macOS 12+, Python 3.10+ (python.org installer or `brew install python`).
# Optional:
#   vendor/ffprobe              ships ffprobe inside the app (must be a standalone build,
#                               not Homebrew's, which depends on other Homebrew files)
#   PXLSYNC_SIGN_ID=...         "Developer ID Application: Your Name (TEAMID)" to sign
#   PXLSYNC_NOTARY_PROFILE=...  notarytool keychain profile to notarize (needs signing)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script must run on a Mac." >&2
  exit 1
fi

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' pxlsync/__init__.py)"
APP="dist/pxlsync.app"
DMG="dist/pxlsync-${VERSION}.dmg"
PY="${PYTHON:-python3}"

echo "▸ pxlsync ${VERSION}  ($(uname -m))"

echo "▸ Setting up build environment"
"$PY" -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements-dev.txt

echo "▸ Running tests"
python -m pytest -q

if [[ -f vendor/ffprobe ]]; then
  chmod +x vendor/ffprobe
  echo "▸ Bundling vendor/ffprobe"
else
  echo "▸ No vendor/ffprobe: the app will use ffprobe installed on each Mac (brew install ffmpeg)"
fi

echo "▸ Building app"
rm -rf build dist
pyinstaller --noconfirm --clean pxlsync.spec >/dev/null

if [[ -n "${PXLSYNC_SIGN_ID:-}" ]]; then
  echo "▸ Signing with ${PXLSYNC_SIGN_ID}"
  codesign --force --deep --timestamp --options runtime \
    --entitlements packaging/entitlements.plist --sign "$PXLSYNC_SIGN_ID" "$APP"
else
  # Ad-hoc signature: required for Apple Silicon to run it at all; not trusted by Gatekeeper.
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "▸ Making disk image"
STAGE="build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "pxlsync ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null

if [[ -n "${PXLSYNC_SIGN_ID:-}" ]]; then
  codesign --force --sign "$PXLSYNC_SIGN_ID" "$DMG"
  if [[ -n "${PXLSYNC_NOTARY_PROFILE:-}" ]]; then
    echo "▸ Notarizing (takes a few minutes)"
    xcrun notarytool submit "$DMG" --keychain-profile "$PXLSYNC_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
  fi
fi

deactivate
echo
echo "✔ Installer ready: $DMG"
echo "  Open it and drag pxlsync into Applications."
if [[ -z "${PXLSYNC_SIGN_ID:-}" ]]; then
  echo "  Unsigned build: on other Macs, the first launch needs"
  echo "  System Settings → Privacy & Security → Open Anyway."
fi
