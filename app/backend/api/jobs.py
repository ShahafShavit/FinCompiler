"""Pipeline actions invoked by the control server (background thread)."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Callable, Optional

import config
import pipeline
from pipeline.trade_portfolio_import import import_newest_trade_portfolio

log = logging.getLogger(__name__)

Sink = Callable[[str], None]

_JOB_SSE_LOGGERS = [
    "pipeline.fetch",
    "pipeline.route_inbox",
    "pipeline.spreadsheet_ingest",
    "pipeline.workbook_normalize",
    "pipeline.fingerprint",
    "pipeline.compiler",
    "api.categorize",
]


def _maybe_backup_first(options: dict[str, Any], sink: Sink) -> None:
    if not bool(options.get("backup_first")):
        return
    from pipeline.backup import create_critical_paths_backup

    root, manifest = create_critical_paths_backup()
    sink(f"BACKUP: snapshot -> {root}")
    sink(f"BACKUP: included {manifest.get('included_top_level')!r}")


def run_action(
    action: str,
    options: dict[str, Any],
    *,
    sink: Sink,
    control_state: Any = None,
) -> None:
    """
    ``options`` keys (all optional unless noted):
      fetch_holdings, fetch_trade_portfolio, fetch_max_isracard, fetch_bank_credit, fetch_bank_osh (bools)
      process_holdings, process_transactions, process_trade_portfolio (bools)
      from_date, to_date (osh)
      auto_categorize, no_fetch (bools)
      backup_first (bool) — snapshot compiled/static/web data before compile- or trade-portfolio-import jobs
    """
    if action == "pipeline":
        dl = bool(options.get("download_enabled", False))
        if dl:
            h = bool(options.get("fetch_holdings", False))
            ftp = bool(options.get("fetch_trade_portfolio", False))
            mi = bool(options.get("fetch_max_isracard", False))
            bc = bool(options.get("fetch_bank_credit", False))
            bo = bool(options.get("fetch_bank_osh", False))
            if not (h or ftp or mi or bc or bo):
                sink("PIPELINE: browser download is on — pick at least one source (holdings, cards, …)")
                return
            pipeline.run_portal_fetches(
                holdings=h,
                trade_portfolio=ftp,
                max_isracard=mi,
                bank_credit=bc,
                bank_osh=bo,
                from_date=options.get("from_date") or None,
                to_date=options.get("to_date") or None,
                sink=sink,
            )
        route = bool(options.get("route_inbox", True))
        proc_h = bool(options.get("process_holdings", False))
        proc_t = bool(options.get("process_transactions", False))
        proc_tp = bool(options.get("process_trade_portfolio", False))
        if not dl and not route and not proc_h and not proc_t and not proc_tp:
            sink("PIPELINE: enable at least one step below")
            return
        if bool(options.get("backup_first")) and (proc_h or proc_t or proc_tp):
            _maybe_backup_first(options, sink)
        if route:
            pipeline.route_inbox(sink=sink)
        elif dl and (proc_h or proc_t):
            sink(
                "PIPELINE: warning — inbox routing is off; new downloads stay in data/input/ "
                "until you route or enable “Route inbox”"
            )
        if proc_tp:
            import_newest_trade_portfolio(sink=sink, db_path=config.ledger_db_file)
        if proc_h:
            pipeline.run_holdings_pipeline(fetch=False, route=False, sink=sink)
        if proc_t:
            pipeline.run_transactions_pipeline(
                route=False,
                auto_categorize=bool(options.get("auto_categorize", False)),
                sink=sink,
            )
        return

    if action == "fetch":
        h = bool(options.get("fetch_holdings"))
        ftp = bool(options.get("fetch_trade_portfolio"))
        mi = bool(options.get("fetch_max_isracard"))
        bc = bool(options.get("fetch_bank_credit"))
        bo = bool(options.get("fetch_bank_osh"))
        if not (h or ftp or mi or bc or bo):
            sink("FETCH: no sources selected; enable at least one checkbox")
            return
        pipeline.run_portal_fetches(
            holdings=h,
            trade_portfolio=ftp,
            max_isracard=mi,
            bank_credit=bc,
            bank_osh=bo,
            from_date=options.get("from_date") or None,
            to_date=options.get("to_date") or None,
            sink=sink,
        )
        return

    if action == "route":
        pipeline.route_inbox(dry_run=bool(options.get("dry_run")), sink=sink)
        return

    if action == "holdings_pipeline":
        pipeline.run_holdings_pipeline(
            fetch=bool(options.get("fetch_holdings")),
            route=not bool(options.get("no_route")),
            ingest=not bool(options.get("no_ingest")),
            compile_=not bool(options.get("no_compile")),
            sink=sink,
        )
        return

    if action == "transactions_pipeline":
        _maybe_backup_first(options, sink)
        do_cat = bool(options.get("categorize_interactive"))
        pipeline.run_transactions_pipeline(
            fetch_max_isracard=bool(options.get("fetch_max_isracard")),
            fetch_bank_credit=bool(options.get("fetch_bank_credit")),
            fetch_bank_osh=bool(options.get("fetch_bank_osh")),
            fetch_lti_portfolio=bool(options.get("fetch_trade_portfolio")),
            from_date=options.get("from_date") or None,
            to_date=options.get("to_date") or None,
            route=not bool(options.get("no_route")),
            ingest=not bool(options.get("no_ingest")),
            compile_=not bool(options.get("no_compile")),
            auto_categorize=bool(options.get("auto_categorize")) and not do_cat,
            sink=sink,
        )
        if do_cat:
            _run_categorize_from_control(sink, control_state)
        return

    if action == "both_process":
        _maybe_backup_first(options, sink)
        auto_only = bool(options.get("auto_categorize")) and not bool(
            options.get("categorize_interactive")
        )
        pipeline.run_all_pipelines_after_shared_downloads(
            auto_categorize=auto_only,
            sink=sink,
        )
        if bool(options.get("categorize_interactive")):
            _run_categorize_from_control(sink, control_state)
        return

    if action == "full_pipeline":
        _maybe_backup_first(options, sink)
        if not bool(options.get("no_fetch")):
            h = bool(options.get("fetch_holdings", True))
            ftp = bool(options.get("fetch_trade_portfolio", False))
            mi = bool(options.get("fetch_max_isracard", True))
            bc = bool(options.get("fetch_bank_credit", True))
            bo = bool(options.get("fetch_bank_osh", True))
            if not (h or ftp or mi or bc or bo):
                sink("FULL PIPELINE: no fetch sources selected; skipping browser downloads")
            else:
                pipeline.run_portal_fetches(
                    holdings=h,
                    trade_portfolio=ftp,
                    max_isracard=mi,
                    bank_credit=bc,
                    bank_osh=bo,
                    from_date=options.get("from_date") or None,
                    to_date=options.get("to_date") or None,
                    sink=sink,
                )
        auto_only = bool(options.get("auto_categorize")) and not bool(
            options.get("categorize_interactive")
        )
        pipeline.run_all_pipelines_after_shared_downloads(
            auto_categorize=auto_only,
            sink=sink,
        )
        if bool(options.get("categorize_interactive")):
            _run_categorize_from_control(sink, control_state)
        return

    if action == "categorize":
        _run_categorize_from_control(sink, control_state)
        return

    if action == "sheets_preview":
        from api import sheets

        snap = sheets.desktop_preview()
        sink(json.dumps(snap, ensure_ascii=False, indent=2)[:8000])
        if not snap.get("ok"):
            raise RuntimeError(str(snap.get("message") or snap.get("error") or "sheets preview failed"))
        return

    if action == "sheets_push":
        from api import sheets

        force = bool(options.get("force"))
        ok, msg, preview = sheets.desktop_push(force=force)
        sink(msg)
        if preview is not None:
            sink(json.dumps(preview, ensure_ascii=False, indent=2)[:8000])
        if not ok:
            raise RuntimeError(msg or "sheets push failed")
        return

    if action == "book_sync":
        from ledger.api_sync import BookSyncError, sync_month

        book_id = str(options.get("book") or "personal")
        month = str(options.get("month") or "").strip()
        try:
            sync_month(book_id, month, sink=sink)
        except BookSyncError as exc:
            raise RuntimeError(str(exc)) from exc
        return

    if action == "book_backfill":
        from ledger.api_sync import BookSyncError, backfill

        book_id = str(options.get("book") or "personal")
        start = str(options.get("from") or "").strip()
        end = str(options.get("to") or "").strip()
        try:
            backfill(book_id, start, end, sink=sink)
        except BookSyncError as exc:
            raise RuntimeError(str(exc)) from exc
        return

    raise ValueError(f"unknown action: {action!r}")


def _run_categorize_from_control(sink: Sink, control_state: Any = None) -> None:
    """Auto-assign what we can; anything left is visible at ``/categorize/`` (no blocking session)."""
    pipeline.run_auto_categorize_with_web_remainder(sink=sink)


def start_background_job(state: Any, action: str, options: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Acquire the control job lock and run ``action`` on a non-daemon thread."""
    from logger import attach_sink_log_handlers, detach_sink_log_handlers

    ok, info = state.try_start_job(action)
    if not ok:
        return 409, {"ok": False, "error": info}
    job_id = info

    def worker() -> None:
        def sink(msg: str) -> None:
            state.log_line(msg)

        err: BaseException | None = None
        log_pairs = attach_sink_log_handlers(sink, _JOB_SSE_LOGGERS)
        try:
            run_action(action, options, sink=sink, control_state=state)
        except BaseException as e:  # noqa: BLE001
            err = e
            state.log_line(traceback.format_exc())
        finally:
            detach_sink_log_handlers(log_pairs)
            state.finish_job(err)

    state.spawn_job_thread(worker, name=f"job-{job_id}")
    return 202, {"ok": True, "job_id": job_id}
