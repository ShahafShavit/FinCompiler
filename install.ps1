# Create .venv and install dependencies from requirements.txt.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m venv .venv
& "$Root\.venv\Scripts\Activate.ps1"
python -m pip install -U pip
python -m pip install -r requirements.txt
Write-Host "Done. Activate with: .venv\Scripts\Activate.ps1"
