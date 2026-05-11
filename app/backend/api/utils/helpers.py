"""Shared helpers for the control HTTP app: state, SPA statics, paths, port check, FastAPI deps."""

from __future__ import annotations

import errno
import logging
import mimetypes
import queue
import re
import socket
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Optional
from urllib.parse import unquote

from fastapi import Depends, Request

log = logging.getLogger(__name__)

# --- In-process job + SSE hub -------------------------------------------------


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


def get_control_state(request: Request) -> ControlState:
    return request.app.state.control_state


StateDep = Annotated[ControlState, Depends(get_control_state)]

# --- HTTP path normalization ---------------------------------------------------


def normalize_http_path(parsed_path: str) -> str:
    p = unquote(parsed_path or "/", errors="replace")
    p = p.strip().lstrip("\ufeff")
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    return p if p else "/"

# --- Port probe (Uvicorn startup) ---------------------------------------------


def address_already_in_use(err: OSError) -> bool:
    if getattr(err, "winerror", None) == 10048:
        return True
    return err.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1))


def fail_port_in_use(host: str, port: int, cause: OSError) -> None:
    log.error(
        "Control HTTP port %s:%s is already in use — another process holds it "
        "(often a second `python -m api.main`). Stop that process, then retry.\n"
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


def assert_control_port_available(host: str, port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as e:
        if address_already_in_use(e):
            fail_port_in_use(host, port, e)
        raise
    finally:
        probe.close()

# --- Vite SPA (dist) -----------------------------------------------------------
# ``helpers.py`` lives at ``app/backend/api/utils/`` → four parents up to ``app/``.


_SPA_DIST_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"
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
  <p>The control server expected <code>app/frontend/dist/index.html</code> but it is missing.</p>
  <div class="card">
    <strong>Dev:</strong> run the Vite dev server in a second terminal and open
    <a href="http://127.0.0.1:5173/">http://127.0.0.1:5173/</a>:
    <pre><code>cd app/frontend
npm install
npm run dev</code></pre>
    Vite proxies <code>/api</code>, <code>/heatmap/api</code>, <code>/categorize</code>,
    <code>/holdings</code>, and the built SPA assets (see <code>app/frontend/vite.config.ts</code>).
  </div>
  <div class="card">
    <strong>Prod:</strong> build once and reload this page:
    <pre><code>cd app/frontend
npm install
npm run build</code></pre>
  </div>
  <p>With <code>PYTHONPATH=app/backend python -m api.main</code> (from repo root) you get APIs and static assets; the UI is the React app (built or Vite dev).</p>
</body>
</html>
"""
)

SPA_ROUTES: frozenset[str] = frozenset(
    {
        "/",
        "/index.html",
        "/settings",
        "/settings/",
        "/pipeline",
        "/pipeline/",
        "/heatmap",
        "/heatmap/",
        "/heatmap/index.html",
        "/heatmap/detail",
        "/heatmap/detail/",
        "/integrity",
        "/integrity/",
        "/categorize",
        "/categorize/",
        "/categorize/index.html",
        "/holdings",
        "/holdings/",
        "/holdings/index.html",
    }
)


def spa_dist_dir() -> Path:
    return _SPA_DIST_DIR


def spa_assets_dir() -> Path:
    return _SPA_ASSETS_DIR


def spa_index_bytes() -> bytes:
    if _SPA_INDEX_FILE.is_file():
        return _SPA_INDEX_FILE.read_bytes()
    return _SPA_DEV_FALLBACK.encode("utf-8")


def safe_subpath(root: Path, rel: str) -> Path | None:
    if not rel:
        return None
    candidate = (root / rel.lstrip("/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def content_type_for(path: Path) -> str:
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
