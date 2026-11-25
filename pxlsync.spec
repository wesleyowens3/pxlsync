# PyInstaller build file. Run:  pyinstaller pxlsync.spec
# Puts the app in dist/ (pxlsync.app on macOS, pxlsync/pxlsync.exe on Windows).
import sys
from pathlib import Path

datas = [("pxlsync/assets", "pxlsync/assets")]
# Optional: drop ffprobe (macOS) or ffprobe.exe (Windows) into vendor/ to ship it inside the app.
for name in ("ffprobe", "ffprobe.exe"):
    if Path("vendor", name).is_file():
        datas.append((f"vendor/{name}", "vendor"))

a = Analysis(["run_pxlsync.py"], datas=datas, excludes=["tkinter"])
pyz = PYZ(a.pure)
icon = "pxlsync/assets/icon.png"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pxlsync", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="pxlsync")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="pxlsync.app", icon=icon, bundle_identifier="live.pxlbox.pxlsync",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.1.0"})
