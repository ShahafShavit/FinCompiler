#!/usr/bin/env python3
"""
Developer Selenium locator recorder — Chrome only.

Run from repo root::

    python tools/recorder.py
    python tools/recorder.py --url https://example.com --out data/private/leumi-steps.jsonl

Each captured interaction appends one JSON object (JSON Lines). Recordings may
contain account-related text; do not commit JSONL captures to git.

Limitations:

- **Cross-origin iframes:** This script runs in each document Selenium attaches
  to via ``Page.addScriptToEvaluateOnNewDocument`` only for navigations within
  the main browser tab's security context. Payloads inside another origin's
  frame are not captured from here; switch frame in Selenium and record there,
  or note the iframe manually.

- **Shadow DOM:** Events are resolved with ``composedPath()``; locator fields
  include ``shadow_boundary`` hints when the event target sits under a shadow
  root.

- **New tabs/windows:** Opened windows do not inherit this poll loop; attach
  recorders separately if you automate multiple targets (stretch goal).

The pipeline-aligned Chrome prefs (download directory) match ``fetch.load_driver``
when ``--pipeline-chrome`` is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
from typing import IO, Any, Optional

# Repo root = parent of tools/
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "app", "backend") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "app", "backend"))

import config  # noqa: E402


def _abs_download_inbox() -> str:
    p = config.download_inbox_dir
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(os.getcwd(), p))


def _tail_file(path: str, max_chars: int = 14000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return ""
    data = data.strip()
    if len(data) > max_chars:
        data = "... (truncated)\n" + data[-max_chars:]
    return data


def _print_chrome_launch_help(log_path: str | None, exc: BaseException) -> None:
    print("\nRecorder: Chrome/WebDriver failed to start.", file=sys.stderr)
    print(f" Selenium/Glue error: {exc}", file=sys.stderr)
    tail = ""
    if log_path and os.path.isfile(log_path):
        print(f"\nChromedriver log file: {log_path}", file=sys.stderr)
        tail = _tail_file(log_path)
        if tail:
            print("\n--- chromedriver log (tail / full small file) ---", file=sys.stderr)
            print(tail, file=sys.stderr)
            print("--- end chromedriver log ---\n", file=sys.stderr)
    print(
        "Typical fixes on Windows:\n"
        "- Update Google Chrome (Help → About Chrome). Selenium 4.x should fetch a matching ChromeDriver.\n"
        "- If Chrome is not the standard install, set FINANCE_RECORDER_CHROME_BINARY or --chrome-binary to chrome.exe.\n"
        "- If Windows / antivirus blocks the driver, try --chromedriver PATH to a known-good chromedriver.exe.\n"
        "- Capture a persistent log:  --chromedriver-log C:\\Temp\\cd.log --chromedriver-verbose\n",
        file=sys.stderr,
    )
    if not tail:
        if log_path is None:
            print(
                "Chromedriver was logging to stderr/stdout — scroll up for [chromedriver] lines, "
                "or re-run with:  --chromedriver-log ...",
                file=sys.stderr,
            )
        else:
            print(
                "Log file missing or empty; try --chromedriver-verbose and a writable --chromedriver-log path.",
                file=sys.stderr,
            )


def _build_chrome(
    *,
    pipeline_chrome: bool,
    window_size: str | None,
    maximize: bool,
    chrome_binary: str | None,
    chromedriver_path: str | None,
    chromedriver_log: str | None,
    chromedriver_verbose: bool,
):
    """
    Start Chrome via Selenium. Uses Chrome Service with optional logging so we can
    print chromedriver output when Chrome exits immediately / session not created.
    """
    from selenium import webdriver
    from selenium.common.exceptions import SessionNotCreatedException
    from selenium.webdriver.chrome.service import Service as ChromeService

    download_dir = _abs_download_inbox() if pipeline_chrome else ""
    chrome_options = webdriver.ChromeOptions()
    if pipeline_chrome:
        os.makedirs(download_dir, exist_ok=True)
        chrome_options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )

    # Chrome binary location (Portable / alternate install / Canary).
    cbin = (chrome_binary or os.environ.get("FINANCE_RECORDER_CHROME_BINARY", "") or "").strip()
    if cbin:
        chrome_options.binary_location = os.path.normpath(os.path.expandvars(cbin))

    cdriver_path = (
        chromedriver_path or os.environ.get("FINANCE_RECORDER_CHROMEDRIVER", "") or ""
    ).strip()

    chromelog_path: Optional[str] = None
    own_temp_log = False
    svc_log: str | IO[str]

    if chromedriver_log is None:
        fd, chromelog_path = tempfile.mkstemp(
            suffix=".log", prefix="finance-recorder-chromedriver-", text=True
        )
        os.close(fd)
        own_temp_log = True
        svc_log = chromelog_path
    else:
        key = str(chromedriver_log).strip().lower()
        if key in ("-", "stderr", "err", "2", "console"):
            svc_log = sys.stderr  # subprocess has STDOUT/DEVNULL but no STDERR for log_output.
        elif key in ("stdout", "out", "1"):
            svc_log = sys.stdout
        else:
            chromelog_path = os.path.normpath(os.path.expandvars(str(chromedriver_log).strip()))
            svc_log = chromelog_path

    service_kwargs: dict[str, Any] = {"log_output": svc_log}
    if chromedriver_verbose or os.environ.get(
        "FINANCE_RECORDER_CHROMEDRIVER_VERBOSE", ""
    ).strip() in ("1", "true", "yes"):
        service_kwargs["service_args"] = ["--verbose"]
    if cdriver_path:
        service_kwargs["executable_path"] = os.path.normpath(os.path.expandvars(cdriver_path))

    service = ChromeService(**service_kwargs)

    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except SessionNotCreatedException as exc:
        time.sleep(0.3)
        _print_chrome_launch_help(chromelog_path, exc)
        raise SystemExit(2) from exc

    if window_size:
        try:
            w, h = window_size.lower().replace("×", "x").split("x")
            driver.set_window_size(int(w.strip()), int(h.strip()))
        except ValueError:
            raise SystemExit(
                f"--window-size expects WxH (e.g. 1280x900); got {window_size!r}"
            )
    elif pipeline_chrome and maximize:
        driver.maximize_window()
    elif maximize:
        driver.maximize_window()

    if own_temp_log and chromelog_path and os.path.isfile(chromelog_path):
        try:
            os.unlink(chromelog_path)
        except OSError:
            pass

    return driver


def _recorder_js(capture_inputs: bool) -> str:
    ci = "true" if capture_inputs else "false"
    return f"""
(function() {{
  if (window.__financeRecorderDrain) return;

  function escCssId(id) {{
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(id));
    return String(id).replace(/^\\d/, "\\\\3$& ");
  }}

  /* XPath 1.0: single-quote in a quoted literal is doubled. */
  function escXPathString(s) {{
    return "'" + String(s).replace(/'/g, "''") + "'";
  }}

  function truncate(s, max) {{
    if (s == null) return null;
    s = String(s);
    if (s.length <= max) return s;
    return s.slice(0, max) + "…";
  }}

  function directText(el) {{
    var t = "";
    for (var c = el.firstChild; c; c = c.nextSibling) {{
      if (c.nodeType === 3) t += c.nodeValue;
    }}
    return (t || "").trim();
  }}

  function shortSubtreeText(el, max) {{
    var raw = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
    return truncate(raw, max);
  }}

  function frameChain() {{
    var chain = [];
    var w = window;
    try {{
      while (true) {{
        try {{ chain.unshift(w.location.href); }} catch (e0) {{ chain.unshift("(opaque)"); }}
        if (w === window.top) break;
        try {{ w = w.parent; }} catch (e1) {{ break; }}
        if (!w) break;
      }}
    }} catch (e) {{ }}
    return chain;
  }}

  function shadowHostsOuterToInner(el) {{
    var stack = [];
    var cur = el;
    while (cur && cur.nodeType === 1) {{
      var root = cur.getRootNode && cur.getRootNode();
      if (root && root.nodeType === 11) {{
        var h = root.host;
        stack.push({{
          tagName: h.tagName,
          id: h.id || null,
          className: h.className && typeof h.className === "string" ? h.className : ""
        }});
        cur = h;
        continue;
      }}
      break;
    }}
    return stack.reverse();
  }}

  function resolveEventTarget(ev) {{
    var t = ev.target;
    try {{
      if (ev.composedPath && ev.composedPath().length) t = ev.composedPath()[0];
    }} catch (e) {{ }}
    return t;
  }}

  function elementOrFallback(t, ev) {{
    if (t && t.nodeType === 1) return t;
    if (ev && ev.target && ev.target.nodeType === 1) return ev.target;
    return null;
  }}

  function nthOfType(el) {{
    var name = el.tagName.toLowerCase();
    var p = el.parentElement;
    if (!p) return name;
    var same = p.querySelectorAll(name);
    var i = Array.prototype.indexOf.call(same, el);
    return name + ":nth-of-type(" + (i + 1) + ")";
  }}

  function cssPath(el, maxSegments) {{
    maxSegments = maxSegments || 6;
    if (!el || el.nodeType !== 1) return null;
    if (el.id) return "#" + escCssId(el.id);
    var segs = [];
    var cur = el;
    while (cur && cur.nodeType === 1 && segs.length < maxSegments) {{
      if (cur.id) {{
        segs.unshift("#" + escCssId(cur.id));
        break;
      }}
      segs.unshift(nthOfType(cur));
      cur = cur.parentElement;
    }}
    return segs.join(" > ");
  }}

  function xpathAbsolute(el) {{
    if (!el || el.nodeType !== 1) return null;
    var parts = [];
    var cur = el;
    while (cur && cur.nodeType === 1) {{
      var tag = cur.tagName.toLowerCase();
      var ix = 1;
      var s = cur.previousSibling;
      while (s) {{
        if (s.nodeType === 1 && s.tagName === cur.tagName) ix++;
        s = s.previousSibling;
      }}
      parts.unshift(tag + "[" + ix + "]");
      cur = cur.parentElement;
    }}
    return "/" + parts.join("/");
  }}

  function xpathPreferId(el) {{
    if (!el || el.nodeType !== 1) return xpathAbsolute(el);
    if (el.id) return "//*[@id=" + escXPathString(el.id) + "]";
    var name = el.getAttribute("name");
    if (name && (el.tagName === "INPUT" || el.tagName === "BUTTON" ||
        el.tagName === "TEXTAREA" || el.tagName === "SELECT"))
      return "//" + el.tagName.toLowerCase() + "[@name=" + escXPathString(name) + "]";
    var alabel = el.getAttribute("aria-label");
    if (alabel)
      return "//" + el.tagName.toLowerCase() + "[@aria-label=" + escXPathString(alabel) + "]";
    var title = el.getAttribute("title");
    if (title)
      return "//" + el.tagName.toLowerCase() + "[@title=" + escXPathString(title) + "]";
    return xpathAbsolute(el);
  }}

  function dataAttrs(el) {{
    var out = {{}};
    if (!el || !el.attributes) return out;
    for (var i = 0; i < el.attributes.length; i++) {{
      var a = el.attributes[i];
      if (a.name.indexOf("data-") === 0) out[a.name] = a.value;
    }}
    return out;
  }}

  function seleniumHints(el, css, xpShort, xpAbs) {{
    var hints = [];
    if (el.id)
      hints.push({{ by: "id", expr: el.id, tuple: ["id", el.id] }});
    if (css)
      hints.push({{ by: "css_selector", expr: css, tuple: ["css_selector", css] }});
    if (xpShort)
      hints.push({{ by: "xpath", expr: xpShort, tuple: ["xpath", xpShort] }});
    if (xpAbs && xpAbs !== xpShort)
      hints.push({{ by: "xpath_absolute", expr: xpAbs, tuple: ["xpath", xpAbs] }});
    return hints;
  }}

  var queue = [];
  window.__financeRecorderDrain = function() {{
    var out = queue;
    queue = [];
    return out;
  }};

  function recordFromInteraction(evType, ev) {{
    var rawT = resolveEventTarget(ev);
    var el = elementOrFallback(rawT, ev);
    if (!el) {{
      return {{
        event: evType,
        timestamp_utc_iso: new Date().toISOString(),
        page_url: (function(){{ try {{ return location.href; }} catch(e) {{ return ""; }} }})(),
        error: "target is not an element (text node / document / null)"
      }};
    }}
    try {{
      var href = "";
      try {{ if (el.tagName === "A") href = el.getAttribute("href") || ""; }} catch (eh) {{ }}
      var value = "";
      try {{ if (typeof el.value === "string") value = el.value; }} catch (ev_) {{ }}
      var shadowHosts = shadowHostsOuterToInner(el);
      var record = {{
        event: evType,
        timestamp_utc_iso: new Date().toISOString(),
        page_url: (function(){{ try {{ return location.href; }} catch(e) {{ return ""; }} }})(),
        frame_chain_urls: frameChain(),
        basics: {{
          tagName: el.tagName ? el.tagName.toLowerCase() : null,
          id: el.id || null,
          className: el.className && typeof el.className === "string" ? el.className : null,
          name: el.getAttribute ? el.getAttribute("name") : null,
          type: el.getAttribute ? el.getAttribute("type") : null,
          href: href || null,
          value: truncate(value, 160),
          text_direct: truncate(directText(el), 200),
          text_subtree_trim: truncate(shortSubtreeText(el, 400), 400),
          aria_label: el.getAttribute ? el.getAttribute("aria-label") : null,
          title_attr: el.getAttribute ? el.getAttribute("title") : null,
          role: el.getAttribute ? el.getAttribute("role") : null,
          data_attributes: dataAttrs(el)
        }},
        locators: {{}},
        shadow_boundary: shadowHosts.length ? {{ shadow_hosts_outer_to_inner: shadowHosts }} : null,
        selenium_hints: []
      }};

      var cssSel = cssPath(el);
      var xpAbs = xpathAbsolute(el);
      var xpShort = xpathPreferId(el);
      record.locators.css_selector_path = cssSel;
      record.locators.xpath_absolute = xpAbs;
      record.locators.xpath_prefer_id_short = xpShort;
      record.selenium_hints = seleniumHints(el, cssSel, xpShort, xpAbs);
      record.notes =
        "Prefer basics.id, data_*, aria-label. Cross-origin iframes: switch frame in Selenium.";

      return record;
    }} catch (err) {{
      return {{
        event: evType,
        timestamp_utc_iso: new Date().toISOString(),
        page_url: (function(){{ try {{ return location.href; }} catch(e) {{ return ""; }} }})(),
        error: String(err && err.message ? err.message : err)
      }};
    }}
  }}

  function attachListeners() {{
    if (window.__financeRecorderListenersAttached) return;
    window.__financeRecorderListenersAttached = true;

    document.addEventListener("click", function(ev) {{
      queue.push(recordFromInteraction("click", ev));
    }}, true);

    if ({ci}) {{
      function onIn(ev) {{
        var r = recordFromInteraction("input", ev);
        queue.push(r);
      }}
      function onCh(ev) {{
        var r = recordFromInteraction("change", ev);
        queue.push(r);
      }}
      document.addEventListener("input", onIn, true);
      document.addEventListener("change", onCh, true);
    }}
  }}

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", attachListeners);
  else
    attachListeners();
}})();
"""


def _inject(driver, capture_inputs: bool) -> None:
    src = _recorder_js(capture_inputs)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": src},
    )


def _enrich_python(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize selenium_hints for JSON (By name strings)."""
    hints = record.get("selenium_hints")
    if not isinstance(hints, list):
        return record
    out_hints = []
    for h in hints:
        if not isinstance(h, dict):
            continue
        tup = h.get("tuple")
        if isinstance(tup, list) and len(tup) == 2:
            by_s, expr = tup[0], tup[1]
            by_map = {
                "id": "ID",
                "css_selector": "CSS_SELECTOR",
                "xpath": "XPATH",
                "xpath_absolute": "XPATH",
            }
            out_hints.append(
                {
                    "by": by_map.get(by_s, by_s.upper()),
                    "expr": expr,
                    "tuple": [f"By.{by_map.get(by_s, by_s.upper())}", expr],
                }
            )
    record = dict(record)
    record["selenium_hints"] = out_hints
    return record


def _human_line(rec: dict[str, Any]) -> str:
    b = rec.get("basics") or {}
    ev = rec.get("event", "?")
    url = rec.get("page_url", "")[:72]
    tag = b.get("tagName", "?")
    eid = b.get("id") or ""
    txt = (b.get("text_subtree_trim") or b.get("text_direct") or "")[:80]
    return f"[{ev}] {tag}" + (f"#{eid}" if eid else "") + f" @ {url} — {txt}"


def parse_args(argv: list[str] | None = None):
    default_private = os.path.join(config.private_dir, "selenium_recordings.jsonl")
    parser = argparse.ArgumentParser(
        description="Record user clicks (and optional input/change) for Selenium locator discovery.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", default="", help='Open this URL after start (default: about:blank).')
    parser.add_argument(
        "--out",
        default="",
        help="JSON Lines output file path (truncate unless --append). Default: stdout.",
    )
    parser.add_argument(
        "--use-private-default-out",
        action="store_true",
        help=f"If set with empty --out, write to {default_private} instead of stdout.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to --out instead of truncating.",
    )
    parser.add_argument(
        "--pipeline-chrome",
        action="store_true",
        help="Use the same Chrome download prefs and maximize as pipeline fetch.load_driver().",
    )
    parser.add_argument(
        "--window-size",
        default="",
        metavar="WxH",
        help="Window size instead of maximize (still applies when set).",
    )
    parser.add_argument(
        "--no-maximize",
        action="store_true",
        help="Do not maximize (only affects --pipeline-chrome without --window-size).",
    )
    parser.add_argument(
        "--capture-inputs",
        action="store_true",
        help="Also record input and change events (may capture sensitive values; truncated in JSON).",
    )
    parser.add_argument(
        "--also-print-human",
        action="store_true",
        help="Echo a one-line summary per event to stderr (stdout/file stay JSON only).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        metavar="SEC",
        help="How often to poll the browser event queue.",
    )
    parser.add_argument(
        "--chrome-binary",
        default="",
        metavar="PATH",
        help="Path to chrome.exe (or env FINANCE_RECORDER_CHROME_BINARY).",
    )
    parser.add_argument(
        "--chromedriver",
        default="",
        metavar="PATH",
        help="Path to chromedriver.exe (or env FINANCE_RECORDER_CHROMEDRIVER).",
    )
    parser.add_argument(
        "--chromedriver-log",
        nargs="?",
        const="stderr",
        default=None,
        metavar="PATH",
        help="Chromedriver log: omit flag for a temp file (printed on failure; deleted on success); "
        "use the flag alone for stderr; or pass a file path.",
    )
    parser.add_argument(
        "--chromedriver-verbose",
        action="store_true",
        help="Pass --verbose to chromedriver (or FINANCE_RECORDER_CHROMEDRIVER_VERBOSE=1).",
    )
    args = parser.parse_args(argv)

    args._default_private_out = default_private  # noqa: SLF001
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print(
        "Warning: recordings may contain account-related or personal text. "
        "Do not commit JSONL captures to git.\n",
        file=sys.stderr,
    )

    from selenium.common.exceptions import WebDriverException

    driver = _build_chrome(
        pipeline_chrome=args.pipeline_chrome,
        window_size=args.window_size.strip() or None,
        maximize=not args.no_maximize,
        chrome_binary=args.chrome_binary.strip() or None,
        chromedriver_path=args.chromedriver.strip() or None,
        chromedriver_log=args.chromedriver_log,
        chromedriver_verbose=args.chromedriver_verbose,
    )
    shutdown = threading.Event()

    def on_sig(*_args):
        shutdown.set()

    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    _inject(driver, args.capture_inputs)
    driver.get(args.url.strip() if args.url.strip() else "about:blank")

    out_path: Optional[str] = None
    opened = False
    if args.out:
        out_path = os.path.normpath(os.path.join(os.getcwd(), args.out))
        od = os.path.dirname(out_path)
        if od:
            os.makedirs(od, exist_ok=True)
        outfile = open(out_path, "a" if args.append else "w", encoding="utf-8")
        opened = True
    elif args.use_private_default_out:
        out_path = args._default_private_out  # noqa: SLF001
        od = os.path.dirname(out_path)
        if od:
            os.makedirs(od, exist_ok=True)
        outfile = open(out_path, "a" if args.append else "w", encoding="utf-8")
        opened = True
    else:
        outfile = sys.stdout

    poll = max(0.05, args.poll_interval)
    try:
        while not shutdown.is_set():
            try:
                raw = driver.execute_script(
                    "return typeof __financeRecorderDrain === 'function' "
                    "? __financeRecorderDrain() "
                    ": [];"
                )
            except WebDriverException:
                time.sleep(poll)
                continue

            if not raw:
                time.sleep(poll)
                continue

            batch = raw if isinstance(raw, list) else []
            for rec in batch:
                if not isinstance(rec, dict):
                    continue
                rec = _enrich_python(rec)
                line = json.dumps(rec, ensure_ascii=False) + "\n"
                outfile.write(line)
                outfile.flush()
                if args.also_print_human:
                    print(_human_line(rec), file=sys.stderr)

            time.sleep(poll)
    finally:
        if opened:
            outfile.close()
        try:
            driver.quit()
        except Exception:
            pass

    if out_path:
        print(f"Wrote recordings to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
