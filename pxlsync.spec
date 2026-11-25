# PyInstaller build file. Run:  pyinstaller pxlsync.spec
# Puts the app in dist/ (pxlsync.app on macOS, pxlsync/pxlsync.exe on Windows).
import re
import sys
from pathlib import Path

VERSION = re.search(r'"(.+)"', Path("pxlsync/__init__.py").read_text()).group(1)

datas = [("pxlsync/assets", "pxlsync/assets")]
binaries = []
# Optional: drop ffprobe (macOS) or ffprobe.exe (Windows) into vendor/ to ship it inside the app.
for name in ("ffprobe", "ffprobe.exe"):
    if Path("vendor", name).is_file():
        binaries.append((f"vendor/{name}", "vendor"))

a = Analysis(["run_pxlsync.py"], datas=datas, binaries=binaries, excludes=["tkinter"])
pyz = PYZ(a.pure)
icon = "pxlsync/assets/icon.png"  # PyInstaller converts it to .icns / .ico (needs Pillow)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pxlsync", console=False, icon=icon,
          entitlements_file="packaging/entitlements.plist" if sys.platform == "darwin" else None)
coll = COLLECT(exe, a.binaries, a.datas, name="pxlsync")
if sys.platform == "darwin":
    folder_reason = "pxlsync reads and copies the media folders you choose to sync."
    app = BUNDLE(
        coll, name="pxlsync.app", icon=icon, bundle_identifier="live.pxlbox.pxlsync",
        info_plist={
            "CFBundleName": "pxlsync",
            "CFBundleDisplayName": "pxlsync",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "PXLBOX",
            # Text macOS shows when pxlsync first opens these locations.
            "NSDocumentsFolderUsageDescription": folder_reason,
            "NSDesktopFolderUsageDescription": folder_reason,
            "NSDownloadsFolderUsageDescription": folder_reason,
            "NSNetworkVolumesUsageDescription": folder_reason,
            "NSRemovableVolumesUsageDescription": folder_reason,
        },
    )
