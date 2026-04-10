"""Terminal-friendly display of Hebrew strings (logical → visual order)."""

import re

import pandas as pd
from bidi.algorithm import get_display

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


def terminal_bidi(s):
    if s is None:
        return ""
    if isinstance(s, float) and pd.isna(s):
        return "nan"
    text = str(s)
    if _HEBREW_RE.search(text):
        return get_display(text)
    return text


def terminal_bidi_seq(seq, left="{", right="}", *, sort=True):
    items = sorted(seq, key=lambda v: str(v)) if sort else list(seq)
    parts = []
    for x in items:
        if isinstance(x, float) and pd.isna(x):
            parts.append("nan")
        else:
            parts.append(repr(terminal_bidi(str(x))))
    return left + ", ".join(parts) + right
