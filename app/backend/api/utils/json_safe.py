"""Browser-safe JSON: no NaN/Infinity (invalid in JSON.parse)."""

from __future__ import annotations

import json
import math
from typing import Any


def sanitize_for_json(obj: Any) -> Any:
    """Make ``obj`` JSON-serializable for browsers: replace invalid float tokens and numpy scalars."""
    try:
        import numpy as np

        _np = np
    except ImportError:
        _np = None

    if obj is None:
        return None
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if _np is not None:
        if isinstance(obj, _np.generic):
            return sanitize_for_json(obj.item())
        if isinstance(obj, _np.ndarray):
            return sanitize_for_json(obj.tolist())
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (str, bool, int)):
        return obj
    return obj


def json_bytes_strict(obj: Any) -> bytes:
    """RFC-compliant JSON bytes (``allow_nan=False``) for any client using ``JSON.parse``."""
    return json.dumps(sanitize_for_json(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
