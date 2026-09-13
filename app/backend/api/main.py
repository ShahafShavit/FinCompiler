"""FastAPI app factory and CLI entry: ``PYTHONPATH=app/backend python -m api.main``.

``sys.path`` is primed so ``config`` resolves when ``app/backend`` was not pre-added
(e.g. run from repo root without ``PYTHONPATH``).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from fastapi import FastAPI

import config
from api.routers.control import register_routes
from api.utils import (
    ControlState,
    address_already_in_use,
    assert_control_port_available,
    fail_port_in_use,
)
from ledger import migrate_ledger_db

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        migrate_ledger_db(config.ledger_db_file)
    except Exception:  # noqa: BLE001
        log.exception("Ledger migrate at control server startup failed; serving anyway")
    yield
    state = getattr(app.state, "control_state", None)
    if state is not None:
        state.join_job(timeout=120.0)


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinCompiler control",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.control_state = ControlState()

    if not config.control_bind_is_loopback():
        from starlette.responses import Response as StarletteResponse

        @app.middleware("http")
        async def _lan_bearer(request, call_next):  # type: ignore[no-untyped-def]
            expected = config.control_http_token
            auth = (request.headers.get("authorization") or "").strip()
            token = ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not token:
                token = str(request.query_params.get("token") or "").strip()
            if not expected or token != expected:
                return StarletteResponse(content=b"unauthorized", status_code=401)
            return await call_next(request)

    register_routes(app)
    return app


def main() -> int:
    from logger import configure_pipeline_logging

    configure_pipeline_logging(logging.INFO)
    log.info("FINANCE_WORKSPACE_ROOT: %s", config.workspace_root() or "(unset; cwd layout)")
    host = getattr(config, "control_http_host", "127.0.0.1")
    port = int(getattr(config, "control_http_port", 8780))
    config.validate_control_bind(host)
    assert_control_port_available(host, port)

    import uvicorn

    try:
        uvicorn.run(
            "api.main:create_app",
            factory=True,
            host=host,
            port=port,
            workers=1,
            log_level="info",
        )
    except OSError as e:
        if address_already_in_use(e):
            fail_port_in_use(host, port, e)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
