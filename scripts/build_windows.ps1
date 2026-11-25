# Build pxlsync on Windows (PowerShell). Output: dist\pxlsync\pxlsync.exe
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
pyinstaller --noconfirm pxlsync.spec
Write-Host "Built dist\pxlsync\pxlsync.exe"
