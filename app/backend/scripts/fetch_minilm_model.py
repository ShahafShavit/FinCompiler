"""Development helper: download MiniLM ONNX weights into ``data/models/minilm``.

Runtime train/score never downloads. Run once per machine from the repo root:

  PYTHONPATH=app/backend python app/backend/scripts/fetch_minilm_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def main() -> int:
    from ledger.category_model import MINILM_MODEL_NAME, fetch_minilm_model

    path = fetch_minilm_model()
    print(f"Fetched {MINILM_MODEL_NAME} into {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
