"""Browser-based categorization UI: local HTTP server.

The categorizer thread posts one pending prompt at a time (see ``_pending``). The page **does not
poll**: it calls ``GET /api/next`` once on load and once after each successful ``POST /api/respond``
or ``POST /api/revise`` to refresh the pending prompt, session category list, and in-session history.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    h2 { font-size: 1rem; font-weight: 600; margin: 0 0 0.5rem 0; opacity: 0.95; }
    .card { background: #25262b; border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem 0; }
    label { display: block; margin: 0.5rem 0 0.25rem; font-size: 0.85rem; opacity: 0.85; }
    input, select, button { font: inherit; padding: 0.45rem 0.6rem; border-radius: 6px; border: 1px solid #3f4046; background: #1a1b1e; color: inherit; width: 100%; box-sizing: border-box; }
    button { cursor: pointer; background: #4c6ef5; border-color: #4c6ef5; color: #fff; margin-top: 0.75rem; }
    button.secondary { background: transparent; color: #adb5bd; border-color: #495057; }
    button.small { margin-top: 0.35rem; padding: 0.35rem 0.5rem; font-size: 0.85rem; width: auto; }
    .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
    .mono { font-family: ui-monospace, Consolas, monospace; font-size: 0.9rem; white-space: pre-wrap; word-break: break-word; }
    .mono-ltr { direction: ltr; unicode-bidi: isolate; }
    .hint { font-size: 0.8rem; opacity: 0.7; margin-top: 0.5rem; }
    #status { margin-top: 1rem; font-size: 0.9rem; opacity: 0.8; }
    .hist-item { border-top: 1px solid #3f4046; padding: 0.75rem 0; }
    .hist-item:first-child { border-top: none; padding-top: 0; }
  </style>
</head>
<body>
  <h1>קטגוריזציה ידנית</h1>
  <div id="app" class="card"><p id="wait">טוען…</p></div>
  <div id="hist"></div>
  <p id="status"></p>
  <script>
  const app = document.getElementById('app');
  const histEl = document.getElementById('hist');
  const statusEl = document.getElementById('status');

  function esc(s) {
    if (s === null || s === undefined) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }
  function escAttr(s) {
    return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
  }

  function normalizeApi(data) {
    if (data && data.pending !== undefined) return data;
    if (data && data.kind) {
      return { pending: data, history: [], session_categories: [] };
    }
    return { pending: { kind: 'idle' }, history: [], session_categories: [] };
  }

  function mergeCategoryOptions(sessionCats, pending) {
    const seen = new Set();
    const out = [];
    function add(c) {
      if (c === null || c === undefined || c === '') return;
      const t = String(c);
      if (seen.has(t)) return;
      seen.add(t);
      out.push(t);
    }
    (sessionCats || []).forEach(add);
    if (pending && pending.kind === 'fluid') {
      (pending.dynamic_categories || []).forEach(add);
      (pending.all_categories || []).forEach(add);
    }
    if (pending && pending.kind === 'new_store') {
      (pending.all_categories || []).forEach(add);
    }
    return out;
  }

  function categorySelectHtml(selectId, selected, sessionCats, pending) {
    const opts = mergeCategoryOptions(sessionCats, pending);
    let h = '<select id="' + escAttr(selectId) + '">';
    h += '<option value="">— בחר קטגוריה —</option>';
    for (const c of opts) {
      const sel = c === selected ? ' selected' : '';
      h += '<option value="' + escAttr(c) + '"' + sel + '>' + esc(c) + '</option>';
    }
    h += '<option value="__custom__">אחר (הקלד)…</option></select>';
    h += '<input id="' + escAttr(selectId) + '-custom" type="text" style="display:none;margin-top:0.5rem" placeholder="קטגוריה חדשה"/>';
    return h;
  }

  function wireCategorySelect(selectId) {
    const sel = document.getElementById(selectId);
    const cust = document.getElementById(selectId + '-custom');
    if (!sel || !cust) return;
    const sync = () => {
      cust.style.display = sel.value === '__custom__' ? 'block' : 'none';
    };
    sel.onchange = sync;
    sync();
  }

  function readCategorySelect(selectId) {
    const sel = document.getElementById(selectId);
    const cust = document.getElementById(selectId + '-custom');
    if (!sel) return '';
    if (sel.value === '__custom__') return (cust && cust.value || '').trim();
    return (sel.value || '').trim();
  }

  function transactionDetailsHtml(data) {
    let h = '';
    h += '<label>תאריך</label><div class="mono mono-ltr" dir="ltr">' + esc(data.date) + '</div>';
    h += '<label>בחובה / בזכות</label><div class="mono mono-ltr" dir="ltr">' + esc(data.expense) + ' / ' + esc(data.income) + '</div>';
    if (data.details != null && data.details !== '') {
      h += '<label>תאור מורחב</label><div class="mono">' + esc(data.details) + '</div>';
    }
    if (data.digits != null && data.digits !== '') {
      h += '<label>4 ספרות</label><div class="mono mono-ltr" dir="ltr">' + esc(data.digits) + '</div>';
    }
    return h;
  }

  async function fetchNext() {
    try {
      const r = await fetch('/api/next', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
      const data = await r.json();
      render(normalizeApi(data));
    } catch (e) {
      app.innerHTML = '<p class="hint">שגיאת תקשורת עם השרת. ודא שהקומפילציה רצה ורענן את הדף.</p>';
      statusEl.textContent = String(e);
    }
  }

  async function postRevise(body) {
    statusEl.textContent = 'מעדכן…';
    try {
      const r = await fetch('/api/revise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      statusEl.textContent = 'עודכן.';
      fetchNext();
    } catch (e) {
      statusEl.textContent = 'שגיאה: ' + e;
    }
  }

  function renderHistory(history, sessionCats, pending) {
    if (!histEl) return;
    if (!history || !history.length) {
      histEl.innerHTML = '';
      histEl.className = '';
      return;
    }
    histEl.className = 'card';
    let h = '<h2>בסשן הנוכחי (ניתן לתקן)</h2>';
    for (const item of history) {
      const pid = escAttr(item.prompt_id);
      h += '<div class="hist-item" data-pid="' + pid + '">';
      if (item.kind === 'fluid') {
        const sid = 'hcat-' + item.prompt_id;
        h += '<p class="mono">' + esc(item.store_name) + '</p>';
        h += '<label>קטגוריה</label>' + categorySelectHtml(sid, (item.response && item.response.category) || '', sessionCats, { kind: 'fluid', dynamic_categories: [], all_categories: [] });
        h += '<button type="button" class="small" data-action="rev-fluid" data-prompt-id="' + pid + '">עדכן קטגוריה</button>';
      } else if (item.kind === 'new_store') {
        const sid = 'hncat-' + item.prompt_id;
        const st = item.response && item.response.is_static;
        h += '<p class="mono">' + esc(item.store_name) + ' <span class="hint">(חנות חדשה)</span></p>';
        h += '<label>קטגוריה</label>' + categorySelectHtml(sid, (item.response && item.response.category) || '', sessionCats, { kind: 'new_store', all_categories: [] });
        h += '<label>סוג</label><select id="hnst-' + pid + '"><option value="1"' + (st === 1 ? ' selected' : '') + '>סטטית (1)</option><option value="0"' + (st === 0 ? ' selected' : '') + '>דינמית (0)</option></select>';
        h += '<button type="button" class="small" data-action="rev-new" data-prompt-id="' + pid + '">עדכן</button>';
      } else if (item.kind === 'resolve_static') {
        h += '<p><span class="mono">' + esc(item.store_name) + '</span> — <span class="mono">' + esc(item.category) + '</span></p>';
        h += '<div class="row2"><button type="button" class="secondary small" data-action="rev-rs" data-is-static="0" data-prompt-id="' + pid + '">דינמית (0)</button><button type="button" class="small" data-action="rev-rs" data-is-static="1" data-prompt-id="' + pid + '">סטטית (1)</button></div>';
      }
      h += '</div>';
    }
    histEl.innerHTML = h;
    for (const item of history) {
      if (item.kind === 'fluid') wireCategorySelect('hcat-' + item.prompt_id);
      if (item.kind === 'new_store') wireCategorySelect('hncat-' + item.prompt_id);
    }
    function pidFromBtn(btn) {
      const raw = btn.getAttribute('data-prompt-id') || '';
      const found = history.find((x) => x.prompt_id === raw);
      return found ? found.prompt_id : null;
    }
    histEl.querySelectorAll('[data-action="rev-fluid"]').forEach((btn) => {
      btn.onclick = () => {
        const id = pidFromBtn(btn);
        const item = history.find((x) => x.prompt_id === id);
        if (!item) return;
        const cat = readCategorySelect('hcat-' + item.prompt_id);
        if (!cat) { statusEl.textContent = 'בחר קטגוריה'; return; }
        postRevise({ kind: 'fluid', prompt_id: item.prompt_id, category: cat });
      };
    });
    histEl.querySelectorAll('[data-action="rev-new"]').forEach((btn) => {
      btn.onclick = () => {
        const id = pidFromBtn(btn);
        const item = history.find((x) => x.prompt_id === id);
        if (!item) return;
        const cat = readCategorySelect('hncat-' + item.prompt_id);
        if (!cat) { statusEl.textContent = 'בחר קטגוריה'; return; }
        const nst = document.getElementById('hnst-' + escAttr(item.prompt_id));
        const is_static = nst ? parseInt(nst.value, 10) : 1;
        postRevise({ kind: 'new_store', prompt_id: item.prompt_id, category: cat, is_static: is_static });
      };
    });
    histEl.querySelectorAll('[data-action="rev-rs"]').forEach((btn) => {
      btn.onclick = () => {
        const id = pidFromBtn(btn);
        const item = history.find((x) => x.prompt_id === id);
        if (!item) return;
        const is_static = parseInt(btn.getAttribute('data-is-static') || '0', 10);
        postRevise({ kind: 'resolve_static', prompt_id: item.prompt_id, is_static: is_static });
      };
    });
  }

  function render(data) {
    const p = data.pending || { kind: 'idle' };
    const sessionCats = data.session_categories || [];
    renderHistory(data.history || [], sessionCats, p);

    if (!p || p.kind === 'idle') {
      app.innerHTML = '<p>אין כרגע שורה לקטגוריה.</p><p class="hint">כשתופיע שורה חדשה, היא תוצג כאן לאחר שמירה או רענון.</p>';
      return;
    }
    if (p.kind === 'fluid') {
      app.innerHTML = `
        <p class="mono">${esc(p.store_name)}</p>
        ${transactionDetailsHtml(p)}
        <label for="cat">קטגוריה</label>
        ${categorySelectHtml('cat', '', sessionCats, p)}
        <p class="hint">קטגוריות דינמיות קיימות: ${esc((p.dynamic_categories || []).join(', '))}</p>
        <button type="button" id="go">שמור</button>`;
      wireCategorySelect('cat');
      document.getElementById('go').onclick = () => {
        const cat = readCategorySelect('cat');
        if (!cat) { statusEl.textContent = 'בחר או הקלד קטגוריה'; return; }
        submit({ kind: 'fluid', prompt_id: p.prompt_id, category: cat });
      };
      return;
    }
    if (p.kind === 'resolve_static') {
      app.innerHTML = `
        <p>האם הקטגוריה <strong class="mono">${esc(p.category)}</strong> עבור <span class="mono">${esc(p.store_name)}</span> היא קבועה (סטטית)?</p>
        ${transactionDetailsHtml(p)}
        <div class="row2">
          <button type="button" class="secondary" id="b0">דינמית (0)</button>
          <button type="button" id="b1">סטטית (1)</button>
        </div>`;
      document.getElementById('b0').onclick = () => submit({ kind: 'resolve_static', prompt_id: p.prompt_id, is_static: 0 });
      document.getElementById('b1').onclick = () => submit({ kind: 'resolve_static', prompt_id: p.prompt_id, is_static: 1 });
      return;
    }
    if (p.kind === 'new_store') {
      app.innerHTML = `
        <p>חנות חדשה: <span class="mono">${esc(p.store_name)}</span></p>
        ${transactionDetailsHtml(p)}
        <label>קטגוריה</label>
        ${categorySelectHtml('ncat', '', sessionCats, p)}
        <label>סוג</label>
        <select id="nst">
          <option value="1">סטטית (1)</option>
          <option value="0">דינמית (0)</option>
        </select>
        <button type="button" id="ngo">שמור</button>`;
      wireCategorySelect('ncat');
      document.getElementById('ngo').onclick = () => {
        const cat = readCategorySelect('ncat');
        if (!cat) { statusEl.textContent = 'בחר או הקלד קטגוריה'; return; }
        submit({
          kind: 'new_store',
          prompt_id: p.prompt_id,
          category: cat,
          is_static: parseInt(document.getElementById('nst').value, 10)
        });
      };
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
      fetchNext();
    } catch (e) {
      statusEl.textContent = 'שגיאה: ' + e;
    }
  }

  fetchNext();
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
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.base_url = ""
        self._categorize_file: Any = None
        self._history: list[dict[str, Any]] = []
        self._session_categories: list[str] = []

    def attach_categorizer(self, categorize_file: Any) -> None:
        """Link the live :class:`~categorizer.CategorizeFile` so /api/revise can update rows."""
        self._categorize_file = categorize_file

    def _push_session_cat(self, cat: str) -> None:
        c = (cat or "").strip()
        if not c:
            return
        if c not in self._session_categories:
            self._session_categories.append(c)

    def _record_completion(self, display: dict[str, Any], kind: str, result: Any) -> None:
        pid = display.get("prompt_id")
        entry: dict[str, Any] = {
            "prompt_id": pid,
            "kind": kind,
            "transaction_id": display.get("transaction_id"),
            "store_name": display.get("store_name"),
            "category": display.get("category"),
            "response": {},
        }
        if kind == "fluid":
            entry["response"] = {"category": str(result)}
            self._push_session_cat(str(result))
        elif kind == "resolve_static":
            entry["response"] = {"is_static": int(result)}
        elif kind == "new_store":
            cat, st = result
            entry["response"] = {"category": str(cat), "is_static": int(st)}
            self._push_session_cat(str(cat))
        self._history.append(entry)

    def _next_payload(self) -> dict[str, Any]:
        with self._lock:
            pending: dict[str, Any] = {"kind": "idle"}
            if self._pending:
                pending = dict(self._pending["display"])
            return {
                "pending": pending,
                "session_categories": list(self._session_categories),
                "history": list(self._history),
            }

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
                path = self.path.split("?", 1)[0]
                if path == "/" or path.startswith("/?"):
                    self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/next":
                    payload = outer._next_payload()
                    self._send(
                        200,
                        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8",
                    )
                    return
                self._send(404, b"Not Found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/api/respond":
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
                    return
                if self.path == "/api/revise":
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    try:
                        data = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        self._send(400, json.dumps({"ok": False, "error": "invalid JSON"}).encode("utf-8"), "application/json")
                        return
                    err = outer._complete_revise(data)
                    if err:
                        self._send(400, json.dumps({"ok": False, "error": err}).encode("utf-8"), "application/json")
                    else:
                        self._send(200, json.dumps({"ok": True}).encode("utf-8"), "application/json")
                    return
                self._send(404, b"Not Found", "text/plain")

        # ThreadingHTTPServer: browsers keep connections alive; a second fetch must not block
        # behind the first socket waiting for the next request (std HTTPServer is single-threaded).
        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
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
        # Short timeouts let the interpreter process SIGINT on Windows while blocked.
        while not self._waiter.wait(timeout=0.25):
            pass
        with self._lock:
            self._pending = None
            out = self._result
            self._result = None
        if isinstance(out, BaseException):
            raise out
        return out

    def _complete_prompt(self, data: dict[str, Any]) -> Optional[str]:
        kind = data.get("kind")
        prompt_id = data.get("prompt_id")
        with self._lock:
            if not self._pending or self._pending.get("id") != prompt_id:
                return "stale or unknown prompt_id"
            display = dict(self._pending["display"])
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
        else:
            self._record_completion(display, str(kind), self._result)
        self._waiter.set()
        return None

    def _complete_revise(self, data: dict[str, Any]) -> Optional[str]:
        cf = self._categorize_file
        if cf is None:
            return "categorizer not attached"
        prompt_id = data.get("prompt_id")
        kind = data.get("kind")
        if not prompt_id or not kind:
            return "prompt_id and kind required"
        with self._lock:
            entry = next((h for h in self._history if h.get("prompt_id") == prompt_id), None)
        if entry is None:
            return "unknown prompt_id"
        if entry.get("kind") != kind:
            return "kind mismatch"
        try:
            if kind == "fluid":
                new_cat = (data.get("category") or "").strip()
                if not new_cat:
                    return "category required"
                tid = str(entry.get("transaction_id") or "")
                store = str(entry.get("store_name") or "")
                prev = (entry.get("response") or {}).get("category")
                cf.apply_session_category_revision(
                    tid,
                    store,
                    new_cat,
                    previous_category=str(prev) if prev is not None else None,
                )
                entry["response"] = {"category": new_cat}
                self._push_session_cat(new_cat)
            elif kind == "new_store":
                new_cat = (data.get("category") or "").strip()
                v = data.get("is_static")
                if not new_cat:
                    return "category required"
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                tid = str(entry.get("transaction_id") or "")
                store = str(entry.get("store_name") or "")
                prev_cat = (entry.get("response") or {}).get("category")
                if str(prev_cat or "").strip() != new_cat:
                    cf.apply_session_category_revision(
                        tid,
                        store,
                        new_cat,
                        previous_category=str(prev_cat) if prev_cat is not None else None,
                    )
                cf.apply_session_new_store_static_revision(store, new_cat, int(v))
                entry["response"] = {"category": new_cat, "is_static": int(v)}
                self._push_session_cat(new_cat)
            elif kind == "resolve_static":
                v = data.get("is_static")
                if v not in (0, 1):
                    return "is_static must be 0 or 1"
                store = str(entry.get("store_name") or "")
                cat = str(entry.get("category") or "")
                cf.apply_session_resolve_static_revision(store, cat, int(v))
                entry["response"] = {"is_static": int(v)}
            else:
                return "unknown kind"
        except ValueError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("revise failed: %s", e)
            return str(e)
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
        # Unblock the categorizer thread if it is stuck in _waiter.wait() (Ctrl+C may not
        # interrupt that wait reliably on all platforms).
        with self._lock:
            if not self._waiter.is_set():
                self._result = KeyboardInterrupt()
                self._waiter.set()
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
