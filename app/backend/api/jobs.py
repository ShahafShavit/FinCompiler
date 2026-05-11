"""Pipeline actions invoked by the control server (background thread)."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

import config
import pipeline

log = logging.getLogger(__name__)

Sink = Callable[[str], None]


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
      fetch_holdings, fetch_max_isracard, fetch_bank_credit, fetch_bank_osh (bools)
      from_date, to_date (osh)
      auto_categorize, no_fetch (bools)
      backup_first (bool) — snapshot compiled/static/web data before compile-oriented jobs
    """
    if action == "pipeline":
        dl = bool(options.get("download_enabled", False))
        if dl:
            h = bool(options.get("fetch_holdings", False))
            mi = bool(options.get("fetch_max_isracard", False))
            bc = bool(options.get("fetch_bank_credit", False))
            bo = bool(options.get("fetch_bank_osh", False))
            if not (h or mi or bc or bo):
                sink("PIPELINE: browser download is on — pick at least one source (holdings, cards, …)")
                return
            pipeline.run_portal_fetches(
                holdings=h,
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
        if not dl and not route and not proc_h and not proc_t:
            sink("PIPELINE: enable at least one step below")
            return
        if bool(options.get("backup_first")) and (proc_h or proc_t):
            _maybe_backup_first(options, sink)
        if route:
            pipeline.route_inbox(sink=sink)
        elif dl and (proc_h or proc_t):
            sink(
                "PIPELINE: warning — inbox routing is off; new downloads stay in data/input/ "
                "until you route or enable “Route inbox”"
            )
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
        mi = bool(options.get("fetch_max_isracard"))
        bc = bool(options.get("fetch_bank_credit"))
        bo = bool(options.get("fetch_bank_osh"))
        if not (h or mi or bc or bo):
            sink("FETCH: no sources selected; enable at least one checkbox")
            return
        pipeline.run_portal_fetches(
            holdings=h,
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
            mi = bool(options.get("fetch_max_isracard", True))
            bc = bool(options.get("fetch_bank_credit", True))
            bo = bool(options.get("fetch_bank_osh", True))
            if not (h or mi or bc or bo):
                sink("FULL PIPELINE: no fetch sources selected; skipping browser downloads")
            else:
                pipeline.run_portal_fetches(
                    holdings=h,
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

    raise ValueError(f"unknown action: {action!r}")


def _run_categorize_from_control(sink: Sink, control_state: Any = None) -> None:
    """Auto-assign what we can; anything left is visible at ``/categorize/`` (no blocking session)."""
    pipeline.run_auto_categorize_with_web_remainder(sink=sink)
