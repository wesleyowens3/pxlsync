# pxlsync

PXLBOX's media sync tool. Four steps, one window:

| Step | What it does |
|---|---|
| **01 CHECK** | Scans the source with `ffprobe`. Pick the required video and audio codec from dropdowns (ProRes 422 HQ, DNxHR HQ, H.264 High, PCM 24-bit, …). Every file is marked PASS or FAIL with the reason. |
| **02 RENAME** | Renames to a template such as `{project}_{name}_v{v:3}`. Version numbers continue from what already exists in the source **and** destination, so `v003` on the server makes the next file `v004`. Live preview, conflict detection, undo. |
| **03 ORGANIZE** | Rules like `*.mov;*.mp4` + `ProRes (any)` → `{date:%Y-%m-%d}/VIDEO/{codec:upper}`. First match wins. Preview and undo. |
| **04 SYNC** | Runs FreeFileSync (Mirror or Update). Failing files can **block** the sync, be **skipped** (added to the FreeFileSync exclude filter), or be synced with a warning. **Verify destination** probes the copies and compares them with the source. |

## Requirements on each workstation

1. **FreeFileSync** from <https://freefilesync.org> (official site only). pxlsync finds it in
   `/Applications` (macOS) or `C:\Program Files\FreeFileSync` (Windows), or use **LOCATE** on the SYNC page.
2. **ffprobe** (part of ffmpeg), unless it's bundled into the build (see `vendor/README.md`).
   - macOS: `brew install ffmpeg`
   - Windows: `winget install Gyan.FFmpeg`

## Run from source

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python run_pxlsync.py
python -m pytest            # 11 tests, uses real ffmpeg-encoded clips
```

## Build the app

- macOS: `scripts/build_mac.sh` → `dist/pxlsync.app`
- Windows: `scripts\build_windows.ps1` → `dist\pxlsync\pxlsync.exe`

Build each on its own OS (PyInstaller can't cross-compile). Unsigned Mac builds need
right-click → Open the first time; sign and notarize for wider rollout.

## Customizing

- **Studio codec presets**: create `codec_presets.json` in the pxlsync config folder
  (macOS `~/Library/Application Support/PXLBOX/pxlsync/`, Windows `%APPDATA%\PXLBOX\pxlsync\`):
  ```json
  [{"label": "ACME delivery master", "kind": "video", "codecs": ["prores"], "profiles": ["HQ", "4444"]}]
  ```
  ffprobe names: `prores` (profiles `Proxy`, `LT`, `Standard`, `HQ`, `4444`, `XQ`),
  `dnxhd` (`DNXHD`, `DNXHR LB/SQ/HQ/HQX/444`), `h264`, `hevc`, `av1`, `pcm_s24le`, `aac`, …
- **FreeFileSync settings**: save a batch job from FreeFileSync with your preferred comparison,
  deletion (Recycle Bin / versioning) and error settings, then choose it as the *Settings template*.
  pxlsync only swaps in the folders and the exclude filter.
- **Rename tokens**: `{project} {client} {name} {ext} {folder} {date} {date:%Y-%m-%d} {mdate}
  {v} {v:3} {seq:4} {codec} {profile} {res} {w} {h} {fps} {kind}`, plus `:upper` / `:lower`
  on text tokens. Names are cleaned of characters Windows rejects, so Mac and PC can share folders.

## Safety

- Renames and moves are journaled before any file is touched (config folder → `journals/`), roll back
  automatically if something fails halfway, and **UNDO LAST** reverses the latest batch.
- A file already named per the template is left alone.
- **SYNC** is disabled until the source is scanned, the destination is set, FreeFileSync is found,
  and (with *Block the sync*) every file passes.

## Layout

```
pxlsync/codecs.py     codec presets for the dropdowns
pxlsync/probe.py      ffprobe wrapper and folder scanning
pxlsync/check.py      pass/fail rules
pxlsync/tokens.py     template tokens shared by rename and organize
pxlsync/rename.py     version-aware rename planner
pxlsync/organize.py   rule-based folder planner
pxlsync/fileops.py    journaled moves and undo
pxlsync/ffs.py        FreeFileSync batch file and launcher
pxlsync/ui/           PySide6 interface and PXLBOX theme
```

## License notes

pxlsync runs an unmodified, separately installed FreeFileSync (GPLv3), so pxlsync's own code
isn't bound by the GPL. JetBrains Mono is bundled under its license (`pxlsync/assets/fonts/`).
