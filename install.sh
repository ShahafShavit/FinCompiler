#!/usr/bin/env bash
# Create .venv, regenerate requirements.txt from imports, install dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

python -m venv .venv
if [[ -f .venv/Scripts/activate ]]; then
  # Git Bash / Windows
  # shellcheck source=/dev/null
  source .venv/Scripts/activate
elif [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
else
  echo "Could not find venv activate script." >&2
  exit 1
fi

python -m pip install -U pip
python "$ROOT/app/backend/scripts/generate_requirements.py"
python -m pip install -r requirements.txt
echo "Done. Activate with: source .venv/Scripts/activate (or .venv\\Scripts\\activate on Windows)"
