# pxlsync

PXLBOX's media sync tool. Four steps, one window.

**Folder pairs** sit at the top, FreeFileSync-style: source folders on the left, destination
folders on the right. **+** adds a pair, **−** removes one, **⇄** swaps a pair (the one in the
header swaps them all). Type a path, **BROWSE**, or drag a folder from Finder/Explorer onto a field.
Every step works across all pairs; the same source can go to several destinations
(e.g. the NAS and a backup drive).

| Step | What it does |
|---|---|
| **01 CHECK** | Scans the source with `ffprobe`. Pick the required video and audio codec from dropdowns (ProRes 422 HQ, DNxHR HQ, H.264 High, PCM 24-bit, …). Every file is marked PASS or FAIL with the reason. |
| **02 RENAME** | Renames to a template such as `{project}_{name}_v{v:3}`. Version numbers continue from what already exists in the source **and** destination, so `v003` on the server makes the next file `v004`. Live preview, conflict detection, undo. |
| **03 ORGANIZE** | Rules like `*.mov;*.mp4` + `ProRes (any)` → `{date:%Y-%m-%d}/VIDEO/{codec:upper}`. First match wins. Preview and undo. |
| **04 COMPARE+SYNC** | Side-by-side compare of every pair, grouped under a header line per pair, like FreeFileSync: every file shows NEW / UPDATE / DELETE / CONFLICT / HELD. Filter with the chips, right-click to change any row's action, then **SYNCHRONIZE**. Modes: Mirror, Update, Two way. Files that failed the codec check can **block** the sync, be **held back**, or sync anyway. **Verify destination** probes the copies and compares codecs with the source. |

## Requirements on each workstation

Only **ffprobe** (part of ffmpeg) for the codec check, unless it's bundled into the build
(see `vendor/README.md`). Compare and sync are built in; no other software needed.
   - macOS: `brew install ffmpeg`
   - Windows: `winget install Gyan.FFmpeg`

## Run from source

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python run_pxlsync.py
python -m pytest            # 13 tests, uses real ffmpeg-encoded clips
```

## macOS installer (.dmg)

**On any Mac** (macOS 12+, Python 3.10+), from the repo folder:

```bash
scripts/make_dmg.sh
```

This runs the tests, builds `pxlsync.app` and packs it into `dist/pxlsync-<version>.dmg`, with
the usual "drag to Applications" window. The app runs on Macs with the same chip as the one
that built it (Apple Silicon → Apple Silicon).

**On GitHub**: `.github/workflows/build-mac.yml` builds the same DMG on GitHub's Macs for every
push to `main` (download it under the run's *Artifacts*) and attaches it to a Release when you push a
tag like `v0.1.0`.

**Gatekeeper**: builds are unsigned unless you have an Apple Developer ID. On another Mac the
first launch is blocked; open **System Settings → Privacy & Security → Open Anyway** once.
To remove that step, sign and notarize ($99/yr Apple Developer account):

```bash
PXLSYNC_SIGN_ID="Developer ID Application: PXLBOX (TEAMID)" PXLSYNC_NOTARY_PROFILE=pxlbox scripts/make_dmg.sh
```

(create the profile once with `xcrun notarytool store-credentials pxlbox`).

**ffprobe**: each Mac needs `brew install ffmpeg` for the codec check, or put a standalone
`ffprobe` build in `vendor/` before building to ship it inside the app.

## Windows build

`scripts\build_windows.ps1` → `dist\pxlsync\pxlsync.exe` (run on Windows; PyInstaller can't
cross-compile).

## Customizing

- **Studio codec presets**: create `codec_presets.json` in the pxlsync config folder
  (macOS `~/Library/Application Support/PXLBOX/pxlsync/`, Windows `%APPDATA%\PXLBOX\pxlsync\`):
  ```json
  [{"label": "ACME delivery master", "kind": "video", "codecs": ["prores"], "profiles": ["HQ", "4444"]}]
  ```
  ffprobe names: `prores` (profiles `Proxy`, `LT`, `Standard`, `HQ`, `4444`, `XQ`),
  `dnxhd` (`DNXHD`, `DNXHR LB/SQ/HQ/HQX/444`), `h264`, `hevc`, `av1`, `pcm_s24le`, `aac`, …
- **Ignore list**: extra patterns on the COMPARE+SYNC page, e.g. `*.tmp; *.lrf; Proxies/`
  (a trailing `/` means a folder). `.DS_Store`, `._*`, `Thumbs.db` and system folders are always ignored.
- **Rename tokens**: `{project} {client} {name} {ext} {folder} {date} {date:%Y-%m-%d} {mdate}
  {v} {v:3} {seq:4} {codec} {profile} {res} {w} {h} {fps} {kind}`, plus `:upper` / `:lower`
  on text tokens. Names are cleaned of characters Windows rejects, so Mac and PC can share folders.

## Safety

- Renames and moves are journaled before any file is touched (config folder → `journals/`), roll back
  automatically if something fails halfway, and **UNDO LAST** reverses the latest batch.
- A file already named per the template is left alone.
- Nothing is copied until you press **SYNCHRONIZE** and confirm a summary of what will happen.
- Copies are written to `name.pxlsync-part` and renamed into place only when complete, so a
  cancelled or failed copy never leaves a broken file under the real name. Modification times are
  preserved, so the next compare shows the files as equal.
- Removed **and overwritten** files go to a dated `.pxlsync-trash` folder on that drive by default
  (or the system Trash, or permanent delete, your choice).
- Times within 2 seconds count as equal (exFAT/FAT32 drives round to 2 s); paths match ignoring case.
- With *Block the sync*, SYNCHRONIZE stays disabled while any file being copied failed the codec check.
- Pair setups that would clobber data are refused: a destination equal to or inside its source, two
  pairs writing to the same destination, or a destination inside another pair's source.

## Layout

```
pxlsync/codecs.py     codec presets for the dropdowns
pxlsync/probe.py      ffprobe wrapper and folder scanning
pxlsync/check.py      pass/fail rules
pxlsync/tokens.py     template tokens shared by rename and organize
pxlsync/rename.py     version-aware rename planner
pxlsync/organize.py   rule-based folder planner
pxlsync/fileops.py    journaled moves and undo
pxlsync/compare.py    folder compare and sync-mode planning
pxlsync/syncer.py     copy / remove engine (safe partial files, trash folder, cancel)
pxlsync/ui/pairs.py   folder pair panel (drag & drop, add/remove/swap)
pxlsync/ui/           PySide6 interface and PXLBOX theme
```

## License notes

pxlsync contains no FreeFileSync code; the compare/sync engine is its own. JetBrains Mono is bundled
under its license (`pxlsync/assets/fonts/`).
