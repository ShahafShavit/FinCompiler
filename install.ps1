# Create .venv, regenerate requirements.txt from imports, install dependencies.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m venv .venv
& "$Root\.venv\Scripts\Activate.ps1"
python -m pip install -U pip
python "$Root\scripts\generate_requirements.py"
python -m pip install -r requirements.txt
Write-Host "Done. Activate with: .venv\Scripts\Activate.ps1"
