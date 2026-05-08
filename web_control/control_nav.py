"""Shared top navigation for Python-rendered control pages (heatmap detail, holdings, categorize).

The React SPA at ``/`` ships its own top nav (``web/src/components/TopNav.tsx``) that
mirrors these links. This module is still used by the Python-rendered pages:
- ``/holdings/`` (``web_control/holdings_page.py``)
- ``/heatmap/detail`` (``web_control/heatmap.py:_wrap_detail_document``)

Links use root-absolute paths so they work regardless of any ``<base href>`` on the page.
The Heatmap and Pipeline links target the SPA routes (no trailing slash); React Router
takes over once the SPA is loaded.
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
