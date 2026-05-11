#!/usr/bin/env bash
# Create .venv, install Python deps (requirements.txt), and frontend deps (npm).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Only create the venv when missing. Re-running `python -m venv` on an existing
# .venv tries to replace Scripts/python.exe; on Windows that fails with Errno 13
# if that interpreter is in use or locked.
if [[ ! -f .venv/Scripts/activate && ! -f .venv/bin/activate ]]; then
  python -m venv .venv
fi
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
python -m pip install -r requirements.txt

if [[ -f app/frontend/package.json ]]; then
  (cd app/frontend && npm install --no-fund --no-audit)
else
  echo "Skipping npm install: app/frontend/package.json not found." >&2
fi

echo "Done. Activate Python venv: source .venv/Scripts/activate (or .venv\\Scripts\\activate on Windows). SPA: cd app/frontend && npm run dev"
