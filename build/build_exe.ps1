# Build ResumeTailor.exe via PyInstaller. Run from project root:
#   powershell -ExecutionPolicy Bypass -File build\build_exe.ps1
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

if (-not (Test-Path .\.venv)) {
  Write-Host "Creating .venv ..."
  python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Clean prior build artifacts.
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build\build
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\dist
Remove-Item -Force -ErrorAction SilentlyContinue .\ResumeTailor.spec

pyinstaller `
  --noconfirm `
  --onefile `
  --name ResumeTailor `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "data;data" `
  --hidden-import dotenv `
  app.py

Write-Host ""
Write-Host "Done. Binary: $(Resolve-Path .\dist\ResumeTailor.exe)"
Write-Host "Before running on another machine, copy .env and data\ next to the exe."
