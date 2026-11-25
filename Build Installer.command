#!/bin/bash
# Double-click this file in Finder to build the pxlsync installer (.dmg).
# It opens in Terminal, builds everything, then shows you the finished .dmg.

cd "$(dirname "$0")" || exit 1
clear
echo "pxlsync installer builder"
echo "========================="
echo

# Find a Python 3.10 or newer (python.org installer, Homebrew, or anything on PATH).
PY=""
for candidate in \
    /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    "$(command -v python3 2>/dev/null)"; do
  [ -x "$candidate" ] || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed to build pxlsync, and it isn't installed."
  echo
  echo "1. The Python download page is opening now. Download and run the macOS installer."
  echo "2. Then double-click 'Build Installer' again."
  open "https://www.python.org/downloads/macos/"
  echo
  read -r -p "Press Return to close."
  exit 1
fi

echo "Using $("$PY" --version) at $PY"
echo "This takes a few minutes the first time (it downloads the build tools)."
echo

if PYTHON="$PY" bash scripts/make_dmg.sh; then
  echo
  echo "Done! Opening the folder with the installer..."
  open dist
else
  echo
  echo "Build failed. Copy everything above and send it to whoever is helping you."
fi
echo
read -r -p "Press Return to close."
