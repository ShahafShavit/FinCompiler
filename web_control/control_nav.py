"""Shared top navigation for legacy Python-rendered pages (e.g. heatmap HTML detail).

The React SPA (`web/src/components/TopNav.tsx`) is the primary UI nav. This module remains
for pages still emitted from Python (heatmap legacy detail HTML only).

Links use root-absolute paths so they work regardless of any ``<base href>`` on the page.
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
        '<a href="/pipeline">Pipeline</a>'
        '<span class="sep">·</span>'
        '<a href="/heatmap">Heatmap</a>'
        '<span class="sep">·</span>'
        '<a href="/holdings/">Holdings</a>'
        '<span class="sep">·</span>'
        '<a href="/categorize/">Categorize</a>'
        "</nav>"
    )
