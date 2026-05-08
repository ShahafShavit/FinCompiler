"""Threading HTTP server: dashboard UI, job API, and SSE log stream."""

from __future__ import annotations

import errno
import json
import logging
import mimetypes
import os
import queue
import re
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

import config
from categorization.interactive.http_server import categorization_html
from logger import attach_sink_log_handlers, detach_sink_log_handlers

from web_control import categorize_queue, control_nav, dashboard_api, heatmap, holdings_page, jobs
from web_control.json_safe import json_bytes_strict as _json_bytes_strict

# Forward structured pipeline / Selenium logs to the dashboard SSE (exclude ``pipeline`` — it already uses sink via _notify).
_JOB_SSE_LOGGERS = [
    "pipeline.portal_fetch",
    "pipeline.inbox_router",
    "pipeline.spreadsheet_ingest",
    "pipeline.csv_handler",
    "pipeline.compiler",
    "categorization.categorizer",
]

log = logging.getLogger(__name__)


# --- SPA static serving (web/dist) ---------------------------------------
# Built by Vite (`npm --prefix web run build`). When `dist/` is missing (typical in dev
# while Vite serves on :5173 directly), backend returns a clear placeholder so the user
# sees what to do without a confusing blank page.

_SPA_DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"
_SPA_INDEX_FILE = _SPA_DIST_DIR / "index.html"
_SPA_ASSETS_DIR = _SPA_DIST_DIR / "assets"

_SPA_DEV_FALLBACK = (
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>finance compiler control — SPA build missing</title>
  <style>
    body { font-family: system-ui, Segoe UI, Roboto, sans-serif; background:#121316; color:#e8e8ec;
           max-width: 44rem; margin: 0 auto; padding: 2rem 1.25rem; line-height: 1.5; }
    code { background:#1c1d22; border:1px solid #2b2c33; padding:0.1rem 0.35rem; border-radius:6px; }
    a { color:#a5b4fc; }
    h1 { font-size: 1.2rem; }
    .card { background:#1c1d22; border:1px solid #2b2c33; border-radius:10px; padding:1rem 1.15rem; margin:1rem 0; }
  </style>
</head>
<body>
  <h1>SPA bundle not built</h1>
  <p>The control server expected <code>web/dist/index.html</code> but it is missing.</p>
  <div class="card">
    <strong>Dev:</strong> run the Vite dev server in a second terminal and open
    <a href="http://127.0.0.1:5173/">http://127.0.0.1:5173/</a>:
    <pre><code>cd web
npm install
npm run dev</code></pre>
    Vite proxies <code>/api</code>, <code>/heatmap/api</code>, <code>/heatmap/legacy-detail</code>,
    <code>/categorize</code>, and <code>/holdings</code> back to this server on port 8780.
  </div>
  <div class="card">
    <strong>Prod:</strong> build once and reload this page:
    <pre><code>cd web
npm install
npm run build</code></pre>
  </div>
  <p>Backend pages still work: <a href="/holdings/">/holdings/</a> · <a href="/categorize/">/categorize/</a>.</p>
</body>
</html>
"""
)


def _spa_index_bytes() -> bytes:
    if _SPA_INDEX_FILE.is_file():
        return _SPA_INDEX_FILE.read_bytes()
    return _SPA_DEV_FALLBACK.encode("utf-8")


def _safe_subpath(root: Path, rel: str) -> Path | None:
    """Resolve ``root / rel`` and confirm it stays inside ``root``. Returns None on traversal."""
    if not rel:
        return None
    candidate = (root / rel.lstrip("/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".mjs":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".map":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"


# Anything matching this is a SPA route — serve index.html so React Router can take over.
_SPA_ROUTES: tuple[str, ...] = (
    "/",
    "/index.html",
    "/pipeline",
    "/pipeline/",
    "/heatmap",
    "/heatmap/",
    "/heatmap/index.html",
    "/heatmap/detail",
    "/heatmap/detail/",
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

            if path == "/categorizer":
                self.send_response(302)
                self.send_header("Location", "/categorize/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path == "/holdings":
                self.send_response(302)
                self.send_header("Location", "/holdings/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path in ("/holdings/", "/holdings/index.html"):
                self._send(200, holdings_page.holdings_shell_html().encode("utf-8"), "text/html; charset=utf-8")
                return

            # Heatmap APIs (must precede SPA fallback since `/heatmap` itself is a SPA route).
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

            if path == "/heatmap/api/detail":
                try:
                    status, payload = heatmap.detail_api_payload(
                        parse_qs(parsed.query, keep_blank_values=True)
                    )
                    body = _json_bytes_strict(payload)
                except Exception:  # noqa: BLE001
                    log.exception("GET /heatmap/api/detail failed")
                    body = _json_bytes_strict(
                        {"ok": False, "error": "server_error", "message": "Detail request failed."}
                    )
                    status = 500
                self._send(status, body, "application/json; charset=utf-8")
                return

            if path == "/heatmap/legacy-detail":
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

            if path in _SPA_ROUTES:
                body = _spa_index_bytes()
                self._send(200, body, "text/html; charset=utf-8")
                return

            if path.startswith("/assets/"):
                rel = path[len("/assets/") :]
                target = _safe_subpath(_SPA_ASSETS_DIR, rel)
                if target is None or not target.is_file():
                    self._send(404, b"Not Found", "text/plain; charset=utf-8")
                    return
                self._send(200, target.read_bytes(), _content_type_for(target))
                return

            if path == "/vite.svg" or path == "/favicon.ico":
                target = _SPA_DIST_DIR / path.lstrip("/")
                if target.is_file():
                    self._send(200, target.read_bytes(), _content_type_for(target))
                    return
                self._send(404, b"", "image/x-icon")
                return

            if path.startswith("/api/dashboard/"):
                name = path[len("/api/dashboard/") :].strip("/")
                qs = parse_qs(parsed.query, keep_blank_values=False)
                try:
                    payload = dashboard_api.handle_dashboard_request(name, qs)
                except Exception:  # noqa: BLE001
                    log.exception("GET /api/dashboard/%s failed", name)
                    payload = {
                        "ok": False,
                        "error": "server_error",
                        "message": f"dashboard {name!r} failed (see server log)",
                        "rows": [],
                    }
                self._send(200, _json_bytes_strict(payload), "application/json; charset=utf-8")
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
                        "ledger_db_file": config.ledger_db_file,
                    }
                )
                self._send(200, body, "application/json; charset=utf-8")
                return

            if path == "/api/holdings/meta":
                from pipeline.holdings_csv_import import get_holdings_meta

                body = _json_bytes_strict(get_holdings_meta(config.ledger_db_file))
                self._send(200, body, "application/json; charset=utf-8")
                return

            if path == "/api/holdings/timeline":
                from pipeline.holdings_csv_import import query_holdings_timeline

                qs = parse_qs(parsed.query, keep_blank_values=False)
                from_date = (qs.get("from") or [None])[0]
                to_date = (qs.get("to") or [None])[0]
                activities = [str(x) for x in (qs.get("activity") or []) if str(x).strip()]
                try:
                    df = query_holdings_timeline(
                        config.ledger_db_file,
                        start_date=str(from_date).strip() if from_date else None,
                        end_date=str(to_date).strip() if to_date else None,
                        activity_types=activities,
                    )
                    payload = {
                        "ok": True,
                        "rows": df.to_dict(orient="records"),
                    }
                    self._send(200, _json_bytes_strict(payload), "application/json; charset=utf-8")
                except Exception as e:  # noqa: BLE001
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_request", "message": str(e)}),
                        "application/json; charset=utf-8",
                    )
                return

            if path == "/api/sheets/status":
                from web_control import desktop_sheets_api

                body = _json_bytes_strict(desktop_sheets_api.api_status())
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

            # API / asset misses → 404 (clients expect a real error). Anything else falls
            # through to the SPA so React Router can handle deep links and unknown paths.
            if (
                path.startswith("/api/")
                or path.startswith("/heatmap/api/")
                or path.startswith("/assets/")
                or path.startswith("/holdings/")
                or path.startswith("/categorize/")
            ):
                if "/heatmap" in path or "/heatmap" in self.path:
                    log.warning("control HTTP 404 GET heatmap-like raw=%r path=%r", self.path, path)
                self._send(404, b"Not Found", "text/plain")
                return
            self._send(200, _spa_index_bytes(), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = _normalize_http_path(parsed.path)

            if path == "/heatmap/api/refresh":
                # Must drain the request body (fetch sends ``{}``) or keep-alive breaks: the next
                # request line on the same socket becomes ``{}GET /...`` → HTTP 501.
                clen = int(self.headers.get("Content-Length", "0") or "0")
                if clen > 0:
                    self.rfile.read(clen)

                try:
                    heatmap.invalidate_bundle_cache()
                except Exception:  # noqa: BLE001
                    log.exception("POST /heatmap/api/refresh failed")
                    self._send(
                        500,
                        _json_bytes_strict({"ok": False, "message": "Reload failed (see server log)."}),
                        "application/json; charset=utf-8",
                    )
                    return
                self._send(
                    200,
                    _json_bytes_strict({"ok": True, "message": "Reloaded heatmap data from SQLite ledger."}),
                    "application/json; charset=utf-8",
                )
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

            if path == "/api/sheets/preview":
                from web_control import desktop_sheets_api

                clen = int(self.headers.get("Content-Length", "0") or "0")
                if clen > 0:
                    self.rfile.read(clen)
                snap = desktop_sheets_api.api_preview()
                if snap.get("error") == "not_configured":
                    self._send(503, _json_bytes_strict(snap), "application/json; charset=utf-8")
                    return
                self._send(200, _json_bytes_strict(snap), "application/json; charset=utf-8")
                return

            if path == "/api/holdings/parse-paste-grid":
                from pipeline.holdings_csv_import import parse_holdings_paste_grid

                clen = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(clen) if clen > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                        "application/json; charset=utf-8",
                    )
                    return
                text = str((data or {}).get("text") or "")
                out = parse_holdings_paste_grid(text)
                self._send(200, _json_bytes_strict(out), "application/json; charset=utf-8")
                return

            if path == "/api/holdings/check-conflicts":
                from pipeline.holdings_csv_import import get_holdings_conflicts

                clen = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(clen) if clen > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                        "application/json; charset=utf-8",
                    )
                    return
                rows = data.get("rows") if isinstance(data.get("rows"), list) else []
                try:
                    conflicts = get_holdings_conflicts(rows, config.ledger_db_file)
                except Exception as e:  # noqa: BLE001
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_rows", "message": str(e)}),
                        "application/json; charset=utf-8",
                    )
                    return
                self._send(
                    200,
                    _json_bytes_strict({"ok": True, "conflicts": conflicts, "conflict_count": len(conflicts)}),
                    "application/json; charset=utf-8",
                )
                return

            if path == "/api/holdings/manual-upsert-batch":
                from pipeline.holdings_csv_import import upsert_holdings_rows

                clen = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(clen) if clen > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                        "application/json; charset=utf-8",
                    )
                    return
                rows = data.get("rows") if isinstance(data.get("rows"), list) else []
                overwrite = bool(data.get("overwrite_conflicts"))
                try:
                    out = upsert_holdings_rows(rows, config.ledger_db_file, overwrite_conflicts=overwrite)
                except Exception as e:  # noqa: BLE001
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_rows", "message": str(e)}),
                        "application/json; charset=utf-8",
                    )
                    return
                code = 200 if out.get("ok") else 409
                self._send(code, _json_bytes_strict(out), "application/json; charset=utf-8")
                return

            if path == "/api/holdings/move-date":
                from pipeline.holdings_csv_import import move_holdings_date

                clen = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(clen) if clen > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_json", "message": "invalid JSON body"}),
                        "application/json; charset=utf-8",
                    )
                    return
                source_date = (data or {}).get("source_date")
                target_date = (data or {}).get("target_date")
                overwrite = bool((data or {}).get("overwrite_conflicts"))
                try:
                    out = move_holdings_date(
                        source_date,
                        target_date,
                        config.ledger_db_file,
                        overwrite_conflicts=overwrite,
                    )
                except Exception as e:  # noqa: BLE001
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "error": "invalid_request", "message": str(e)}),
                        "application/json; charset=utf-8",
                    )
                    return
                code = 200 if out.get("ok") else 409
                self._send(code, _json_bytes_strict(out), "application/json; charset=utf-8")
                return

            if path == "/api/sheets/push":
                from web_control import desktop_sheets_api

                clen = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(clen) if clen > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(
                        400,
                        _json_bytes_strict({"ok": False, "message": "invalid JSON body"}),
                        "application/json; charset=utf-8",
                    )
                    return
                opts = data if isinstance(data, dict) else {}
                force = bool(opts.get("force"))
                ok, msg, preview = desktop_sheets_api.api_push(force=force)
                payload: dict = {"ok": ok, "message": msg}
                if preview is not None:
                    payload["preview"] = preview
                if ok:
                    code = 200
                elif preview is not None:
                    code = 409
                elif "not configured" in (msg or "").lower():
                    code = 503
                else:
                    code = 502
                self._send(code, _json_bytes_strict(payload), "application/json; charset=utf-8")
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
