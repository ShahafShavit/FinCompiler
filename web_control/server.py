"""Threading HTTP server: dashboard UI, job API, and SSE log stream."""

from __future__ import annotations

import errno
import json
import logging
import math
import queue
import re
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import config
from interactive_categorization.http_server import categorization_html
from logger import attach_sink_log_handlers, detach_sink_log_handlers

from web_control import categorize_queue, control_nav, heatmap, jobs

# Forward structured pipeline / Selenium logs to the dashboard SSE (exclude ``pipeline`` — it already uses sink via _notify).
_JOB_SSE_LOGGERS = [
    "portal_fetch",
    "inbox_router",
    "spreadsheet_ingest",
    "csv_handler",
    "compile_handler",
    "categorizer",
    "folder_tracking",
]

log = logging.getLogger(__name__)

_DASHBOARD_HTML = (
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Finance pipeline control</title>
  <style>
"""
    + control_nav.control_topnav_css()
    + """
    :root {
      font-family: system-ui, Segoe UI, Roboto, sans-serif;
      background: #121316;
      color: #e8e8ec;
      line-height: 1.45;
    }
    body { max-width: 52rem; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
    h1 { font-size: 1.35rem; font-weight: 600; margin: 0 0 0.25rem 0; }
    .sub { opacity: 0.75; font-size: 0.9rem; margin-bottom: 1.25rem; }
    .card {
      background: #1c1d22;
      border: 1px solid #2b2c33;
      border-radius: 10px;
      padding: 1rem 1.15rem;
      margin: 1rem 0;
    }
    h2 { font-size: 1rem; font-weight: 600; margin: 0 0 0.65rem 0; }
    label.row { display: flex; align-items: center; gap: 0.5rem; margin: 0.35rem 0; cursor: pointer; }
    input[type="text"] {
      font: inherit;
      padding: 0.4rem 0.55rem;
      border-radius: 6px;
      border: 1px solid #3a3b44;
      background: #121316;
      color: inherit;
      width: 100%;
      max-width: 14rem;
      box-sizing: border-box;
    }
    select {
      font: inherit;
      padding: 0.4rem 0.55rem;
      border-radius: 6px;
      border: 1px solid #3a3b44;
      background: #121316;
      color: inherit;
    }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
    @media (max-width: 640px) { .grid2 { grid-template-columns: 1fr; } }
    button {
      font: inherit;
      cursor: pointer;
      padding: 0.5rem 0.85rem;
      border-radius: 8px;
      border: 1px solid #4c6ef5;
      background: #4c6ef5;
      color: #fff;
      margin: 0.25rem 0.35rem 0.25rem 0;
    }
    button.secondary {
      background: transparent;
      color: #adb5bd;
      border-color: #495057;
    }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    #log {
      font-family: ui-monospace, Consolas, monospace;
      font-size: 0.8rem;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 22rem;
      overflow: auto;
      background: #0b0c0f;
      border-radius: 8px;
      padding: 0.65rem 0.75rem;
      border: 1px solid #2b2c33;
    }
    #status { font-size: 0.9rem; margin: 0.5rem 0 0 0; opacity: 0.9; }
    .hint { font-size: 0.82rem; opacity: 0.7; margin-top: 0.5rem; }
    .pill {
      display: inline-block;
      font-size: 0.75rem;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
      background: #2b2c33;
      margin-left: 0.35rem;
    }
    .pill.run { background: #2f4a1f; color: #c7f0a8; }
    .pill.err { background: #4a1f1f; color: #ffb4b4; }
    .indent { margin: 0.35rem 0 0.5rem 1rem; padding: 0.35rem 0 0.35rem 0.75rem; border-left: 2px solid #2b2c33; }
    input.combo { max-width: 11rem; }
    label.row.combo-row { align-items: flex-start; flex-direction: column; gap: 0.35rem; }
  </style>
</head>
<body>
"""
    + control_nav.control_topnav_html()
    + """
  <h1>Finance pipeline control</h1>
  <p class="sub">Local dashboard for fetches, routing, compile, and categorization.
    <a href="/categorize/">Categorization</a>
    <span id="queue_hint" class="pill"></span>
    <span id="conn" class="pill">SSE …</span>
    <span id="busy" class="pill"></span>
  </p>

  <div class="card">
    <h2>Pipeline</h2>
    <p class="hint">Check what this run should do, then click <strong>Run pipeline</strong>. Order is always: optional downloads → optional route → compile holdings (if checked) → compile transactions (if checked).</p>

    <label class="row"><input type="checkbox" id="p_dl"/> <strong>Browser download</strong> — Chrome/Selenium saves exports into <code>data/input/</code></label>
    <div class="indent">
      <label class="row"><input type="checkbox" id="p_dl_h" disabled/> Bank holdings</label>
      <label class="row"><input type="checkbox" id="p_dl_m" disabled/> Max + Isracard</label>
      <label class="row"><input type="checkbox" id="p_dl_bc" disabled/> Leumi credit export</label>
      <label class="row"><input type="checkbox" id="p_dl_osh" disabled/> Leumi account (osh)</label>
      <div class="grid2" style="margin-top:0.5rem">
        <div>
          <label for="from_d">Osh from (DD.MM.YY)</label><br/>
          <input type="text" id="from_d" placeholder="optional" disabled/>
        </div>
        <div>
          <label for="to_d">Osh to (DD.MM.YY)</label><br/>
          <input type="text" id="to_d" placeholder="optional" disabled/>
        </div>
      </div>
    </div>

    <label class="row" style="margin-top:0.75rem"><input type="checkbox" id="p_route" checked/> <strong>Route inbox</strong> — move <code>data/input/*.xls*</code> into workspace holdings / transactions folders</label>
    <p class="hint">Automatically stays on when you compile (so new downloads reach the right pipeline folders).</p>

    <label class="row"><input type="checkbox" id="p_hold"/> <strong>Compile holdings</strong> → <code>export/compiled/holdings.csv</code></label>
    <label class="row"><input type="checkbox" id="p_tx"/> <strong>Compile transactions</strong> → <code>export/compiled/compiled.csv</code></label>
    <label class="row"><input type="checkbox" id="p_auto" disabled/> <strong>Auto-categorize</strong> after transactions compile (rows still missing a category → <a href="/categorize/">/categorize/</a>)</label>

    <label class="row combo-row" style="margin-top:0.65rem">Transaction column-drop profile (type or pick)
      <input type="text" class="combo" id="drop_prof" list="drop_prof_list" value="full" autocomplete="off" title="full = same drops as desktop app; batch = smaller legacy set"/>
      <datalist id="drop_prof_list">
        <option value="full"></option>
        <option value="batch"></option>
      </datalist>
    </label>

    <div style="margin-top:0.85rem">
      <button type="button" id="btn_pipeline">Run pipeline</button>
    </div>
    <p class="hint">Manual categories: <code id="cat_url"></code></p>
  </div>

  <div class="card">
    <h2>Categorization only</h2>
    <p class="hint">Runs an auto pass on <code>compiled.csv</code>. Open <a href="/categorize/">/categorize/</a> any time to answer whatever is still missing a category (no separate &quot;session&quot;).</p>
    <button type="button" id="btn_cat">Run auto-categorize</button>
  </div>

  <div class="card">
    <h2>Live log</h2>
    <div id="log"></div>
    <p id="status"></p>
  </div>

  <script>
  const logEl = document.getElementById('log');
  const statusEl = document.getElementById('status');
  const connEl = document.getElementById('conn');
  const busyEl = document.getElementById('busy');
  const catUrlEl = document.getElementById('cat_url');

  function normalizeDropProfile() {
    const raw = (document.getElementById('drop_prof').value || '').trim().toLowerCase();
    if (raw === 'batch' || raw.indexOf('batch') === 0) return 'batch';
    return 'full';
  }

  function syncPipelineDeps() {
    const dl = document.getElementById('p_dl').checked;
    ['p_dl_h', 'p_dl_m', 'p_dl_bc', 'p_dl_osh', 'from_d', 'to_d'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = !dl;
    });
    const procT = document.getElementById('p_tx').checked;
    const auto = document.getElementById('p_auto');
    auto.disabled = !procT;
    if (!procT) auto.checked = false;
    const procH = document.getElementById('p_hold').checked;
    const route = document.getElementById('p_route');
    if (procH || procT) {
      route.checked = true;
      route.disabled = true;
      route.title = 'Required when compiling so files are sorted into workspace inboxes first.';
    } else {
      route.disabled = false;
      route.title = '';
    }
  }

  document.getElementById('p_dl').addEventListener('change', function () {
    if (this.checked && !this.dataset.primed) {
      this.dataset.primed = '1';
      ['p_dl_h', 'p_dl_m', 'p_dl_bc', 'p_dl_osh'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.checked = true;
      });
    }
    syncPipelineDeps();
  });
  ['p_hold', 'p_tx', 'p_route'].forEach((id) => {
    document.getElementById(id).addEventListener('change', syncPipelineDeps);
  });
  syncPipelineDeps();

  function setBusy(b, job) {
    busyEl.textContent = b ? ('running: ' + (job || 'job')) : '';
    busyEl.className = 'pill' + (b ? ' run' : '');
    document.querySelectorAll('button').forEach((btn) => {
      if (btn.id === 'btn_pipeline' || btn.id === 'btn_cat') btn.disabled = b;
    });
  }

  async function postJob(action, options) {
    statusEl.textContent = '';
    const body = { action, options: options || {} };
    try {
      const r = await fetch('/api/jobs/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      setBusy(true, j.job_id || action);
      statusEl.textContent = 'Started: ' + (j.job_id || action);
    } catch (e) {
      statusEl.textContent = 'Error: ' + e;
    }
  }

  document.getElementById('btn_pipeline').onclick = () => {
    const dl = document.getElementById('p_dl').checked;
    const route = document.getElementById('p_route').checked;
    const procH = document.getElementById('p_hold').checked;
    const procT = document.getElementById('p_tx').checked;
    if (!dl && !route && !procH && !procT) {
      statusEl.textContent = 'Choose at least one step (download, route, or a compile option).';
      return;
    }
    postJob('pipeline', {
      download_enabled: dl,
      fetch_holdings: document.getElementById('p_dl_h').checked,
      fetch_max_isracard: document.getElementById('p_dl_m').checked,
      fetch_bank_credit: document.getElementById('p_dl_bc').checked,
      fetch_bank_osh: document.getElementById('p_dl_osh').checked,
      from_date: document.getElementById('from_d').value.trim() || null,
      to_date: document.getElementById('to_d').value.trim() || null,
      route_inbox: route,
      process_holdings: procH,
      process_transactions: procT,
      auto_categorize: document.getElementById('p_auto').checked,
      drop_profile: normalizeDropProfile(),
    });
  };
  document.getElementById('btn_cat').onclick = () => postJob('categorize', {});

  async function pollStatus() {
    try {
      const r = await fetch('/api/status', { cache: 'no-store' });
      const j = await r.json();
      setBusy(!!j.running, j.current_job);
      if (j.error) {
        busyEl.className = 'pill err';
        busyEl.textContent = 'error: ' + j.error;
      }
    } catch (_) { /* ignore */ }
  }
  setInterval(pollStatus, 2000);
  pollStatus();

  async function pollQueue() {
    try {
      const r = await fetch('/categorize/api/summary', { cache: 'no-store' });
      const j = await r.json();
      const el = document.getElementById('queue_hint');
      if (!el) return;
      if (!j.compiled_exists) { el.textContent = 'no compiled.csv'; el.className = 'pill'; return; }
      el.textContent = j.open_count ? (j.open_count + ' need category') : 'queue empty';
      el.className = 'pill' + (j.open_count ? '' : ' run');
    } catch (_) { /* ignore */ }
  }
  setInterval(pollQueue, 4000);
  pollQueue();

  const es = new EventSource('/api/events');
  es.onopen = () => { connEl.textContent = 'SSE connected'; connEl.className = 'pill run'; };
  es.onerror = () => { connEl.textContent = 'SSE disconnected'; connEl.className = 'pill err'; };
  es.addEventListener('log', (ev) => {
    try {
      const d = JSON.parse(ev.data);
      const line = d.message || '';
      logEl.textContent += line + '\\n';
      logEl.scrollTop = logEl.scrollHeight;
    } catch (_) { }
  });
  es.addEventListener('state', (ev) => {
    try {
      const d = JSON.parse(ev.data);
      setBusy(!!d.running, d.job);
      if (d.error) statusEl.textContent = 'Job error: ' + d.error;
    } catch (_) { }
  });

  fetch('/api/config').then((r) => r.json()).then((c) => {
    catUrlEl.textContent = c.categorize_url_hint || '';
  }).catch(() => {});
  </script>
</body>
</html>
"""
)

class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            stale: list[queue.Queue[dict[str, Any]]] = []
            for sub in self._subs:
                try:
                    sub.put_nowait(event)
                except queue.Full:
                    stale.append(sub)
            for sub in stale:
                self._subs.remove(sub)


class ControlState:
    def __init__(self) -> None:
        self.hub = EventHub()
        self._job_lock = threading.Lock()
        self._running = False
        self._current_job: str = ""
        self._error: str = ""
        self._job_seq = 0

    def try_start_job(self, name: str) -> tuple[bool, str]:
        if not self._job_lock.acquire(blocking=False):
            return False, "Another job is already running"
        self._running = True
        self._current_job = name
        self._error = ""
        self._job_seq += 1
        jid = f"{name}-{self._job_seq}"
        self.hub.publish({"type": "state", "running": True, "job": jid, "error": None})
        return True, jid

    def finish_job(self, err: Optional[BaseException] = None) -> None:
        if err is not None:
            self._error = str(err)
            self.hub.publish({"type": "state", "running": False, "job": "", "error": self._error})
        else:
            self.hub.publish({"type": "state", "running": False, "job": "", "error": None})
        self._running = False
        self._current_job = ""
        try:
            self._job_lock.release()
        except RuntimeError:
            pass

    def log_line(self, msg: str) -> None:
        self.hub.publish({"type": "log", "message": msg, "ts": time.time()})

    @property
    def running(self) -> bool:
        return self._running

    @property
    def current_job(self) -> str:
        return self._current_job

    @property
    def last_error(self) -> str:
        return self._error


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _sanitize_for_json(obj: Any) -> Any:
    """Make ``obj`` JSON-serializable for browsers: Python emits invalid ``NaN`` unless sanitized."""
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
            return _sanitize_for_json(obj.item())
        if isinstance(obj, _np.ndarray):
            return _sanitize_for_json(obj.tolist())
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (str, bool, int)):
        return obj
    return obj


def _json_bytes_strict(obj: Any) -> bytes:
    """RFC-compliant JSON (no ``NaN`` / ``Infinity`` tokens) for browser ``JSON.parse``."""
    return json.dumps(_sanitize_for_json(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _normalize_http_path(parsed_path: str) -> str:
    """
    Stable URL path for routing: trim, strip BOM, ensure leading slash, collapse ``//``.
    Fixes spurious 404s when ``self.path`` differs slightly from ``/heatmap/`` (CR/LF, ``//``, etc.).
    """
    p = unquote(parsed_path or "/", errors="replace")
    p = p.strip().lstrip("\ufeff")
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    return p if p else "/"


def make_handler_class(state: ControlState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("control HTTP %s", self.address_string() + " - " + (fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = _normalize_http_path(parsed.path)

            if path == "/categorize":
                self.send_response(302)
                self.send_header("Location", "/categorize/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            # Heatmap before ``/categorize/*`` so paths never interact.
            if path == "/heatmap":
                self.send_response(302)
                self.send_header("Location", "/heatmap/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path in ("/heatmap/", "/heatmap/index.html"):
                self._send(200, heatmap.heatmap_shell_html().encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/heatmap/heatmap_page_script.js":
                js_path = heatmap.heatmap_page_script_path()
                if not js_path.is_file():
                    log.error("heatmap script missing at %s", js_path)
                    self._send(
                        404,
                        b"// heatmap_page_script.js not found on server\n",
                        "text/plain; charset=utf-8",
                    )
                    return
                self._send(200, js_path.read_bytes(), "application/javascript; charset=utf-8")
                return

            if path == "/heatmap/api/data":
                try:
                    snap = heatmap.api_snapshot()
                    body = _json_bytes_strict(snap)
                except Exception:  # noqa: BLE001
                    log.exception("GET /heatmap/api/data failed")
                    err = {
                        "ok": False,
                        "error": "server_error",
                        "message": "Heatmap snapshot failed (see server log).",
                        "sourceStatus": {},
                        "views": {},
                        "statsHtml": {},
                    }
                    body = _json_bytes_strict(err)
                self._send(200, body, "application/json; charset=utf-8")
                return

            if path == "/heatmap/detail":
                code, body, ct = heatmap.handle_detail_query(parsed.query)
                self._send(code, body, ct)
                return

            if path.startswith("/categorize/"):
                rest = path[len("/categorize") :] or "/"
                if not rest.startswith("/"):
                    rest = "/" + rest
                if rest in ("/", "/index.html"):
                    html = categorization_html("/categorize/")
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if rest.startswith("/api/"):
                    code, body, ct = categorize_queue.handle_get(rest)
                    self._send(code, body, ct)
                    return
                self._send(404, b"Not Found", "text/plain")
                return

            if path in ("/", "/index.html"):
                self._send(200, _DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/status":
                body = _json_bytes(
                    {
                        "running": state.running,
                        "current_job": state.current_job,
                        "error": state.last_error or None,
                    }
                )
                self._send(200, body, "application/json; charset=utf-8")
                return

            if path == "/api/config":
                host = getattr(config, "control_http_host", "127.0.0.1")
                cport = int(getattr(config, "control_http_port", 8780))
                body = _json_bytes(
                    {
                        "control_base": f"http://{host}:{cport}/",
                        "categorize_url_hint": f"http://{host}:{cport}/categorize/",
                        "workspace_root": config.workspace_root() or None,
                        "input_dir": config.download_inbox_dir,
                        "compiled_file": config.compiled_file,
                    }
                )
                self._send(200, body, "application/json; charset=utf-8")
                return

            if path == "/api/events":
                q = state.hub.subscribe()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while True:
                        try:
                            ev = q.get(timeout=20.0)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        et = str(ev.get("type") or "message")
                        payload = json.dumps(ev, ensure_ascii=False)
                        self.wfile.write(f"event: {et}\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except Exception as e:  # noqa: BLE001
                    log.debug("SSE client ended: %s", e)
                finally:
                    state.hub.unsubscribe(q)
                return

            if "/heatmap" in path or "/heatmap" in self.path:
                log.warning("control HTTP 404 GET heatmap-like raw=%r path=%r", self.path, path)
            self._send(404, b"Not Found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = _normalize_http_path(parsed.path)

            if path == "/heatmap/api/refresh":
                # Must drain the request body (fetch sends ``{}``) or keep-alive breaks: the next
                # request line on the same socket becomes ``{}GET /...`` → HTTP 501.
                clen = int(self.headers.get("Content-Length", "0") or "0")
                if clen > 0:
                    self.rfile.read(clen)

                from web_control import totals_sheet_sync

                try:
                    ok, msg = totals_sheet_sync.refresh_totals_from_cloud()
                except Exception:  # noqa: BLE001
                    log.exception("POST /heatmap/api/refresh failed")
                    self._send(
                        500,
                        _json_bytes_strict({"ok": False, "message": "Refresh failed (see server log)."}),
                        "application/json; charset=utf-8",
                    )
                    return
                code = 200 if ok else 502
                self._send(code, _json_bytes_strict({"ok": ok, "message": msg}), "application/json; charset=utf-8")
                return

            if path.startswith("/categorize/"):
                rest = path[len("/categorize") :] or "/"
                if not rest.startswith("/"):
                    rest = "/" + rest
                if rest.startswith("/api/"):
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b""
                    code, body, ct = categorize_queue.handle_post(rest, raw)
                    self._send(code, body, ct)
                    return
                self._send(404, b"Not Found", "text/plain")
                return

            if path != "/api/jobs/run":
                self._send(404, b"Not Found", "text/plain")
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send(400, _json_bytes({"ok": False, "error": "invalid JSON"}), "application/json")
                return
            action = str(data.get("action") or "").strip()
            options = data.get("options") if isinstance(data.get("options"), dict) else {}
            if not action:
                self._send(400, _json_bytes({"ok": False, "error": "action required"}), "application/json")
                return

            ok, info = state.try_start_job(action)
            if not ok:
                self._send(
                    409,
                    _json_bytes({"ok": False, "error": info}),
                    "application/json",
                )
                return
            job_id = info

            def worker() -> None:
                def sink(msg: str) -> None:
                    state.log_line(msg)

                err: Optional[BaseException] = None
                log_pairs = attach_sink_log_handlers(sink, _JOB_SSE_LOGGERS)
                try:
                    jobs.run_action(action, options, sink=sink, control_state=state)
                except BaseException as e:  # noqa: BLE001
                    err = e
                    tb = traceback.format_exc()
                    state.log_line(tb)
                finally:
                    detach_sink_log_handlers(log_pairs)
                    state.finish_job(err)

            threading.Thread(target=worker, name=f"job-{job_id}", daemon=True).start()
            self._send(
                202,
                _json_bytes({"ok": True, "job_id": job_id}),
                "application/json; charset=utf-8",
            )

    return Handler


def _address_already_in_use(err: OSError) -> bool:
    if getattr(err, "winerror", None) == 10048:
        return True
    return err.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1))


def _fail_port_in_use(host: str, port: int, cause: OSError) -> None:
    log.error(
        "Control HTTP port %s:%s is already in use — another process holds it "
        "(often a second `python -m web_control`). Stop that process, then retry.\n"
        "  Windows:  netstat -ano | findstr \":%s\"\n"
        "            Stop-Process -Id <PID> -Force\n"
        "  Unix:     lsof -i :%s   or   ss -lntp | grep %s",
        host,
        port,
        port,
        port,
        port,
    )
    raise SystemExit(1) from cause


def _assert_control_port_available(host: str, port: int) -> None:
    """Fail fast with a clear message if the port is taken (avoids two servers / confusing 404s)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as e:
        if _address_already_in_use(e):
            _fail_port_in_use(host, port, e)
        raise
    finally:
        probe.close()


class ControlHTTPServer(ThreadingHTTPServer):
    """Single-instance friendly: do not reuse the address (no stacked listeners)."""

    allow_reuse_address = False


def serve_forever() -> None:
    host = getattr(config, "control_http_host", "127.0.0.1")
    port = int(getattr(config, "control_http_port", 8780))
    _assert_control_port_available(host, port)
    state = ControlState()
    handler = make_handler_class(state)
    try:
        server = ControlHTTPServer((host, port), handler)
    except OSError as e:
        if _address_already_in_use(e):
            _fail_port_in_use(host, port, e)
        raise
    log.info("Listening on http://%s:%s/ (single instance; stop other servers on this port first)", host, port)
    log.info(
        "Dashboard: http://%s:%s/ — heatmap …/heatmap/ — categorize …/categorize/",
        host,
        port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down control server")
    finally:
        server.server_close()
