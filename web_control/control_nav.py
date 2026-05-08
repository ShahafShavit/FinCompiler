"""Shared top navigation for control dashboard pages (Dashboard · Heatmap · Holdings · Categorize).

Links use root-absolute paths (``/``, ``/heatmap/``, ``/categorize/``) so they work even when
a page sets ``<base href="...">`` (e.g. categorization UI under ``/categorize/``).
"""


def control_topnav_css() -> str:
    return """
    .topnav {
      display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem 0.5rem;
      padding: 0.35rem 0 0.85rem; margin: 0 0 0.25rem 0;
      border-bottom: 1px solid #2b2c33;
    }
    .topnav a { color: #a5b4fc; text-decoration: none; font-size: 0.9rem; }
    .topnav a:hover { text-decoration: underline; }
    .topnav .sep { opacity: 0.45; user-select: none; }
    """


def control_topnav_html() -> str:
    return (
        '<nav class="topnav" aria-label="Main">'
        '<a href="/">Dashboard</a>'
        '<span class="sep">·</span>'
        '<a href="/heatmap/">Heatmap</a>'
        '<span class="sep">·</span>'
        '<a href="/holdings/">Holdings</a>'
        '<span class="sep">·</span>'
        '<a href="/categorize/">Categorize</a>'
        "</nav>"
    )
