import { useCallback, useEffect, useMemo, useState } from 'react';

import TopCategoriesBoard from '../components/TopCategoriesBoard';
import { getJson, patchJson, postJson } from '../lib/api';
import { heatmapDetailCategory, heatmapDetailSource } from '../lib/drilldown';

import './DataIntegrity.css';

type ReportSection = {
  id: string;
  title: string;
  severity: string;
  count: number;
  rows: Record<string, unknown>[];
  note?: string;
};

type ReportResponse = {
  ok?: boolean;
  ledger_exists?: boolean;
  sections?: ReportSection[];
};

type StoreRow = {
  store_name: string;
  is_static: number;
  category_count: number;
  categories: string[];
};

type StoresResponse = {
  ok?: boolean;
  ledger_exists?: boolean;
  stores?: StoreRow[];
};

type TopCategoriesResponse = {
  ok?: boolean;
  ledger_exists?: boolean;
  columns?: { top_name: string; sub_categories: string[] }[];
  unassigned?: string[];
};

function cellStr(v: unknown): string {
  if (v == null) return '—';
  return String(v);
}

function rowTxnLink(row: Record<string, unknown>): string | null {
  const src = row['מקור עסקה'];
  if (typeof src === 'string' && src.trim()) {
    return heatmapDetailSource(src.trim(), 24);
  }
  return null;
}

/** SQLite `id` for a single ledger row (some sections use other key shapes). */
function rowTxnId(row: Record<string, unknown>): number | null {
  const v = row.id;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && /^-?\d+$/.test(v.trim())) return parseInt(v.trim(), 10);
  return null;
}

export default function DataIntegrity() {
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [stores, setStores] = useState<StoreRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [renameFrom, setRenameFrom] = useState('');
  const [renameTo, setRenameTo] = useState('');
  const [renameMsg, setRenameMsg] = useState<string | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [txnMsg, setTxnMsg] = useState<string | null>(null);
  const [txnBusy, setTxnBusy] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [quickId, setQuickId] = useState('');
  const [quickFingerprint, setQuickFingerprint] = useState('');
  const [topCats, setTopCats] = useState<TopCategoriesResponse | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [rpt, st, tc] = await Promise.all([
        getJson<ReportResponse>('/api/integrity/report'),
        getJson<StoresResponse>('/api/integrity/stores'),
        getJson<TopCategoriesResponse>('/api/integrity/top-categories'),
      ]);
      setReport(rpt);
      setStores(st.stores ?? []);
      setTopCats(tc);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const sections = report?.sections ?? [];

  const toggleSection = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const onRename = async (dryRun: boolean) => {
    setRenameBusy(true);
    setRenameMsg(null);
    const r = await postJson<Record<string, unknown>>('/api/integrity/rename-category', {
      from: renameFrom.trim(),
      to: renameTo.trim(),
      dry_run: dryRun,
    });
    setRenameBusy(false);
    const p = r.data;
    if (!r.ok || p.ok === false) {
      setRenameMsg(typeof p.message === 'string' ? p.message : `HTTP ${r.status}`);
      return;
    }
    if (dryRun && p.would_update) {
      setRenameMsg(`Dry run: would update ${JSON.stringify(p.would_update)}`);
      return;
    }
    if (p.rows_updated) {
      setRenameMsg(`Updated ${JSON.stringify(p.rows_updated)}`);
    } else {
      setRenameMsg('Done.');
    }
    void loadAll();
  };

  const onSetStatic = async (storeName: string, isStatic: number) => {
    const r = await patchJson<Record<string, unknown>>('/api/integrity/store-static', {
      store_name: storeName,
      is_static: isStatic,
    });
    const p = r.data;
    if (!r.ok || p.ok === false) {
      window.alert(typeof p.message === 'string' ? p.message : `HTTP ${r.status}`);
      return;
    }
    if (typeof p.forward_filled_uncategorized === 'number' && p.forward_filled_uncategorized > 0) {
      setTxnMsg(`Filled ${p.forward_filled_uncategorized} uncategorized row(s) for ${storeName}.`);
    }
    void loadAll();
  };

  const onPatchExcluded = async (rowId: number, excluded_from_calculations: 0 | 1) => {
    setTxnBusy(true);
    setTxnMsg(null);
    const r = await patchJson<Record<string, unknown>>('/api/integrity/ledger-tx', {
      id: rowId,
      patch: { excluded_from_calculations },
    });
    setTxnBusy(false);
    const p = r.data;
    if (!r.ok || p.ok === false) {
      setTxnMsg(typeof p.message === 'string' ? p.message : `HTTP ${r.status}`);
      return;
    }
    setTxnMsg(excluded_from_calculations === 1 ? `Row ${rowId} excluded from calculations.` : `Row ${rowId} included again.`);
    void loadAll();
  };

  const onQuickExclude = async (excluded_from_calculations: 0 | 1) => {
    const idTrim = quickId.trim();
    const fpTrim = quickFingerprint.trim();
    setTxnMsg(null);
    if (!idTrim && !fpTrim) {
      setTxnMsg('Enter a ledger row ID and/or paste a fingerprint (either is enough).');
      return;
    }
    setTxnBusy(true);
    const body: Record<string, unknown> = { patch: { excluded_from_calculations } };
    if (idTrim) {
      const n = parseInt(idTrim, 10);
      if (!Number.isFinite(n)) {
        setTxnBusy(false);
        setTxnMsg('Row id must be a number.');
        return;
      }
      body.id = n;
    }
    if (fpTrim) body.fingerprint = fpTrim;

    const r = await patchJson<Record<string, unknown>>('/api/integrity/ledger-tx', body);
    setTxnBusy(false);
    const p = r.data;
    if (!r.ok || p.ok === false) {
      setTxnMsg(typeof p.message === 'string' ? p.message : `HTTP ${r.status}`);
      return;
    }
    const rid = typeof p.id === 'number' ? p.id : null;
    const label = rid != null ? `Row ${rid}` : 'Transaction';
    setTxnMsg(excluded_from_calculations === 1 ? `${label} excluded from calculations.` : `${label} included again.`);
    void loadAll();
  };

  const severityClass = useMemo(
    () =>
      ({
        error: 'intg-sev--error',
        warning: 'intg-sev--warning',
        info: 'intg-sev--info',
      }) as Record<string, string>,
    [],
  );

  return (
    <div className="intg-page app-page">
      <header className="intg-header">
        <h1 className="intg-title">Data integrity</h1>
        <p className="intg-sub">
          Review ledger anomalies, rename categories, adjust store static/dynamic mode, and soft-exclude rows from all
          calculations (heatmap, dashboard, categorize queue).{' '}
          <a href="/categorize/">Categorize queue</a>
        </p>
        <div className="intg-toolbar">
          <button type="button" className="intg-btn" onClick={() => void loadAll()} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {err ? <div className="intg-error">{err}</div> : null}
      {!report?.ledger_exists ? <p className="intg-muted">No ledger file found at configured path.</p> : null}
      {txnMsg ? <p className="intg-msg intg-msg--txn">{txnMsg}</p> : null}

      {topCats && report?.ledger_exists ? (
        <TopCategoriesBoard
          ledgerExists={!!topCats.ledger_exists}
          columns={topCats.columns ?? []}
          unassigned={topCats.unassigned ?? []}
          onSaved={() => void loadAll()}
        />
      ) : null}

      <section className="intg-card">
        <h2 className="intg-card-title">Exclude any transaction</h2>
        <p className="intg-hint">
          Paste the SQLite <strong>id</strong> (see any check table below, or open <code>ledger.sqlite</code>) and/or the
          row <strong>fingerprint</strong>. If both are filled, the numeric id is used. The row is kept in the DB but
          dropped from heatmap, dashboard totals, and categorize.
        </p>
        <div className="intg-rename-row">
          <label className="intg-label">
            Row id
            <input
              className="intg-input"
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={quickId}
              onChange={(e) => setQuickId(e.target.value)}
              placeholder="e.g. 1842"
            />
          </label>
          <label className="intg-label">
            Fingerprint
            <input
              className="intg-input"
              value={quickFingerprint}
              onChange={(e) => setQuickFingerprint(e.target.value)}
              placeholder="paste full fingerprint string"
            />
          </label>
        </div>
        <div className="intg-rename-actions">
          <button
            type="button"
            className="intg-btn"
            disabled={txnBusy || !report?.ledger_exists}
            onClick={() => void onQuickExclude(1)}
          >
            Exclude from calculations
          </button>
          <button
            type="button"
            className="intg-btn intg-btn--secondary"
            disabled={txnBusy || !report?.ledger_exists}
            onClick={() => void onQuickExclude(0)}
          >
            Include again
          </button>
        </div>
      </section>

      <section className="intg-card">
        <h2 className="intg-card-title">Rename category</h2>
        <p className="intg-hint">Exact string match on `קטגוריה` and `store_category`.</p>
        <div className="intg-rename-row">
          <label className="intg-label">
            From
            <input
              className="intg-input"
              value={renameFrom}
              onChange={(e) => setRenameFrom(e.target.value)}
              placeholder="old name"
            />
          </label>
          <label className="intg-label">
            To
            <input className="intg-input" value={renameTo} onChange={(e) => setRenameTo(e.target.value)} placeholder="new name" />
          </label>
        </div>
        <div className="intg-rename-actions">
          <button type="button" className="intg-btn intg-btn--secondary" disabled={renameBusy} onClick={() => void onRename(true)}>
            Preview
          </button>
          <button type="button" className="intg-btn" disabled={renameBusy} onClick={() => void onRename(false)}>
            Apply
          </button>
        </div>
        {renameMsg ? <p className="intg-msg">{renameMsg}</p> : null}
      </section>

      <section className="intg-card">
        <h2 className="intg-card-title">Stores (static / dynamic)</h2>
        <p className="intg-hint">
          Static stores may have at most one category. Turning static on backfills uncategorized rows for that payee when possible.
        </p>
        {stores && stores.length === 0 ? <p className="intg-muted">No stores in ledger.</p> : null}
        {stores && stores.length > 0 ? (
          <div className="intg-table-wrap">
            <table className="intg-table">
              <thead>
                <tr>
                  <th>Store</th>
                  <th>Mode</th>
                  <th># cats</th>
                  <th>Categories</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {stores.map((s) => (
                  <tr key={s.store_name}>
                    <td>{s.store_name}</td>
                    <td>{s.is_static === 1 ? 'static' : 'dynamic'}</td>
                    <td>{s.category_count}</td>
                    <td className="intg-cats">{s.categories.length ? s.categories.join(', ') : '—'}</td>
                    <td>
                      {s.is_static === 1 ? (
                        <button type="button" className="intg-btn intg-btn--small" onClick={() => void onSetStatic(s.store_name, 0)}>
                          Set dynamic
                        </button>
                      ) : (
                        <button type="button" className="intg-btn intg-btn--small" onClick={() => void onSetStatic(s.store_name, 1)}>
                          Set static
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="intg-sections">
        <h2 className="intg-card-title">Checks</h2>
        {sections.map((sec) => {
          const open = !!expanded[sec.id];
          const sev = severityClass[sec.severity] ?? 'intg-sev--info';
          const cols =
            sec.rows.length > 0
              ? Array.from(new Set(sec.rows.flatMap((r) => Object.keys(r)))).filter(
                  (k) => k !== 'fingerprint' || sec.id === 'null_fingerprint' || sec.id === 'excluded_transactions',
                )
              : [];
          const canPatchRows =
            sec.rows.length > 0 &&
            (sec.id === 'excluded_transactions' || sec.rows.some((r) => rowTxnId(r) !== null));
          return (
            <div key={sec.id} className={`intg-check ${sev}`}>
              <button type="button" className="intg-check-hd" onClick={() => toggleSection(sec.id)}>
                <span className="intg-check-title">{sec.title}</span>
                <span className="intg-badge">{sec.count}</span>
                <span className="intg-chevron">{open ? '▼' : '▶'}</span>
              </button>
              {sec.note ? <p className="intg-note">{sec.note}</p> : null}
              {open && sec.rows.length > 0 ? (
                <div className="intg-table-wrap">
                  <table className="intg-table">
                    <thead>
                      <tr>
                        {cols.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                        {canPatchRows ? <th>Calculations</th> : null}
                        <th> </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sec.rows.map((row, i) => (
                        <tr key={i}>
                          {cols.map((c) => (
                            <td key={c}>{cellStr(row[c])}</td>
                          ))}
                          {canPatchRows ? (
                            <td className="intg-patch">
                              {sec.id === 'excluded_transactions' && rowTxnId(row) !== null ? (
                                <button
                                  type="button"
                                  className="intg-btn intg-btn--small"
                                  disabled={txnBusy}
                                  onClick={() => void onPatchExcluded(rowTxnId(row)!, 0)}
                                >
                                  Include
                                </button>
                              ) : rowTxnId(row) !== null && sec.id !== 'excluded_transactions' ? (
                                <button
                                  type="button"
                                  className="intg-btn intg-btn--small"
                                  disabled={txnBusy}
                                  onClick={() => void onPatchExcluded(rowTxnId(row)!, 1)}
                                >
                                  Exclude
                                </button>
                              ) : null}
                            </td>
                          ) : null}
                          <td className="intg-links">
                            {sec.id === 'rare_categories' && typeof row.category === 'string' ? (
                              <a href={heatmapDetailCategory('expense', row.category)}>Heatmap</a>
                            ) : null}
                            {sec.id === 'store_category_orphans' && typeof row.category === 'string' ? (
                              <a href={heatmapDetailCategory('expense', row.category)}>Heatmap</a>
                            ) : null}
                            {rowTxnLink(row) ? (
                              <a href={rowTxnLink(row) ?? '#'}>Detail</a>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {open && sec.rows.length === 0 ? <p className="intg-muted">No sample rows.</p> : null}
            </div>
          );
        })}
      </section>
    </div>
  );
}
