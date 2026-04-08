"""Browser-based categorization UI: local HTTP server + JSON queue."""

from __future__ import annotations

import json
import logging
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

from interactive_categorization.prompts import FluidStorePrompt, NewStorePrompt, ResolveStaticPrompt

log = logging.getLogger(__name__)

_HTML = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>קטגוריזציה</title>
  <style>
    :root { font-family: system-ui, Segoe UI, Arial, sans-serif; background: #1a1b1e; color: #e8e8ea; }
    body { max-width: 42rem; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; font-weight: 600; }
    .card { background: #25262b; border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem 0; }
    label { display: block; margin: 0.5rem 0 0.25rem; font-size: 0.85rem; opacity: 0.85; }
    input, select, button { font: inherit; padding: 0.45rem 0.6rem; border-radius: 6px; border: 1px solid #3f4046; background: #1a1b1e; color: inherit; width: 100%; box-sizing: border-box; }
    button { cursor: pointer; background: #4c6ef5; border-color: #4c6ef5; color: #fff; margin-top: 0.75rem; }
    button.secondary { background: transparent; color: #adb5bd; border-color: #495057; }
    .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
    .mono { font-family: ui-monospace, Consolas, monospace; font-size: 0.9rem; white-space: pre-wrap; word-break: break-word; }
    .hint { font-size: 0.8rem; opacity: 0.7; margin-top: 0.5rem; }
    #status { margin-top: 1rem; font-size: 0.9rem; opacity: 0.8; }
  </style>
</head>
<body>
  <h1>קטגוריזציה ידנית</h1>
  <div id="app" class="card"><p id="wait">טוען…</p></div>
  <p id="status"></p>
  <script>
  const app = document.getElementById('app');
  const statusEl = document.getElementById('status');

  function esc(s) {
    if (s === null || s === undefined) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  async function poll() {
    const r = await fetch('/api/current', { cache: 'no-store' });
    const data = await r.json();
    render(data);
  }

  function render(data) {
    if (!data || data.kind === 'idle') {
      app.innerHTML = '<p>ממתין לשורה הבאה מהמערכת…</p><p class="hint">השאר את החלון פתוח בזמן שהקומפילציה מחכה לקלט.</p>';
      return;
    }
    if (data.kind === 'fluid') {
      app.innerHTML = `
        <p class="mono">${esc(data.store_name)}</p>
        <label>תאריך</label><div class="mono">${esc(data.date)}</div>
        <label>בחובה / בזכות</label><div class="mono">${esc(data.expense)} / ${esc(data.income)}</div>
        ${data.details != null && data.details !== '' ? '<label>תאור מורחב</label><div class="mono">' + esc(data.details) + '</div>' : ''}
        ${data.digits != null && data.digits !== '' ? '<label>4 ספרות</label><div class="mono">' + esc(data.digits) + '</div>' : ''}
        <label for="cat">קטגוריה (קיימת או חדשה)</label>
        <input id="cat" list="allcats" autocomplete="off" placeholder="בחר או הקלד"/>
        <datalist id="allcats">${(data.all_categories || []).map(c => '<option value="' + esc(c) + '">').join('')}</datalist>
        <p class="hint">קטגוריות דינמיות קודמות: ${esc((data.dynamic_categories || []).join(', '))}</p>
        <button type="button" id="go">שמור</button>`;
      document.getElementById('go').onclick = () => submit({ kind: 'fluid', prompt_id: data.prompt_id, category: document.getElementById('cat').value.trim() });
      return;
    }
    if (data.kind === 'resolve_static') {
      app.innerHTML = `
        <p>האם הקטגוריה <strong class="mono">${esc(data.category)}</strong> עבור <span class="mono">${esc(data.store_name)}</span> היא קבועה (סטטית)?</p>
        <div class="row2">
          <button type="button" class="secondary" id="b0">דינמית (0)</button>
          <button type="button" id="b1">סטטית (1)</button>
        </div>`;
      document.getElementById('b0').onclick = () => submit({ kind: 'resolve_static', prompt_id: data.prompt_id, is_static: 0 });
      document.getElementById('b1').onclick = () => submit({ kind: 'resolve_static', prompt_id: data.prompt_id, is_static: 1 });
      return;
    }
    if (data.kind === 'new_store') {
      app.innerHTML = `
        <p>חנות חדשה: <span class="mono">${esc(data.store_name)}</span></p>
        <label>קטגוריה</label>
        <input id="ncat" list="newcats" autocomplete="off"/>
        <datalist id="newcats">${(data.all_categories || []).map(c => '<option value="' + esc(c) + '">').join('')}</datalist>
        <label>סוג</label>
        <select id="nst">
          <option value="1">סטטית (1)</option>
          <option value="0">דינמית (0)</option>
        </select>
        <button type="button" id="ngo">שמור</button>`;
      document.getElementById('ngo').onclick = () => submit({
        kind: 'new_store',
        prompt_id: data.prompt_id,
        category: document.getElementById('ncat').value.trim(),
        is_static: parseInt(document.getElementById('nst').value, 10)
      });
      return;
    }
    app.innerHTML = '<p>מצב לא ידוע</p>';
  }

  async function submit(body) {
    statusEl.textContent = 'שולח…';
    try {
      const r = await fetch('/api/respond', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      statusEl.textContent = 'נשמר.';
      poll();
    } catch (e) {
      statusEl.textContent = 'שגיאה: ' + e;
    }
  }

  setInterval(poll, 600);
  poll();
  </script>
</body>
</html>
"""


class HttpCategorizationHandler:
    """Blocks the categorizer thread until each prompt is answered in the browser."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        open_browser: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._open_browser = open_browser
        self._lock = threading.Lock()
        self._pending: Optional[dict[str, Any]] = None
        self._waiter = threading.Event()
        self._result: Any = None
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.base_url = ""

    def _ensure_server(self) -> None:
        if self._server is not None:
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                log.debug("HTTP %s", self.address_string() + " - " + (fmt % args))

            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/" or self.path.startswith("/?"):
                    self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if self.path == "/api/current":
                    with outer._lock:
                        payload = {"kind": "idle"}
                        if outer._pending:
                            payload = dict(outer._pending["display"])
                    self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                    return
                self._send(404, b"Not Found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/api/respond":
                    self._send(404, b"Not Found", "text/plain")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(400, json.dumps({"ok": False, "error": "invalid JSON"}).encode("utf-8"), "application/json")
                    return
                err = outer._complete_prompt(data)
                if err:
                    self._send(400, json.dumps({"ok": False, "error": err}).encode("utf-8"), "application/json")
                else:
                    self._send(200, json.dumps({"ok": True}).encode("utf-8"), "application/json")

        self._server = HTTPServer((self._host, self._port), Handler)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}/"
        self._thread = threading.Thread(target=self._server.serve_forever, name="categorize-http", daemon=True)
        self._thread.start()
        log.info("Categorization UI: %s", self.base_url)
        if self._open_browser:
            webbrowser.open(self.base_url)

    def _wait_on_prompt(self, display: dict[str, Any]) -> Any:
        self._ensure_server()
        pid = display.get("prompt_id") or uuid.uuid4().hex
        display = {**display, "prompt_id": pid}
        with self._lock:
            self._waiter.clear()
            self._result = None
            self._pending = {"display": display, "id": pid}
        self._waiter.wait()
        with self._lock:
            self._pending = None
            out = self._result
            self._result = None
        if isinstance(out, Exception):
            raise out
        return out

    def _complete_prompt(self, data: dict[str, Any]) -> Optional[str]:
        kind = data.get("kind")
        prompt_id = data.get("prompt_id")
        with self._lock:
            if not self._pending or self._pending.get("id") != prompt_id:
                return "stale or unknown prompt_id"
        try:
            if kind == "fluid":
                cat = (data.get("category") or "").strip()
                if not cat:
                    return "category required"
                self._result = cat
            elif kind == "resolve_static":
                v = data.get("is_static")
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                self._result = int(v)
            elif kind == "new_store":
                cat = (data.get("category") or "").strip()
                if not cat:
                    return "category required"
                v = data.get("is_static")
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                self._result = (cat, int(v))
            else:
                return "unknown kind"
        except Exception as e:  # noqa: BLE001
            self._result = e
        self._waiter.set()
        return None

    def prompt_fluid_store(self, prompt: FluidStorePrompt) -> str:
        d = prompt.to_display_dict()
        r = self._wait_on_prompt(d)
        return str(r)

    def prompt_resolve_static(self, prompt: ResolveStaticPrompt) -> int:
        d = prompt.to_display_dict()
        r = self._wait_on_prompt(d)
        return int(r)

    def prompt_new_store(self, prompt: NewStorePrompt) -> tuple[str, int]:
        d = prompt.to_display_dict()
        r = self._wait_on_prompt(d)
        if isinstance(r, tuple) and len(r) == 2:
            return str(r[0]), int(r[1])
        raise TypeError("internal: expected tuple from new_store prompt")

    def close(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception as e:  # noqa: BLE001
                log.debug("HTTP server shutdown: %s", e)
            try:
                self._server.server_close()
            except Exception as e:  # noqa: BLE001
                log.debug("HTTP server socket close: %s", e)
            self._server = None
        self._thread = None
