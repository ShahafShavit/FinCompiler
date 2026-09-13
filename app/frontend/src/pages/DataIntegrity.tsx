import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import PageShell from '../components/PageShell';
import TopCategoriesBoard from '../components/TopCategoriesBoard';
import { fetchJson, patchJson, postJson, isAbortError } from '../lib/api';
import { heatmapDetailCategory, heatmapDetailSource } from '../lib/drilldown';
import { he } from '../lib/he';

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

  const loadAll = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setErr(null);
    try {
      const [rpt, st, tc] = await Promise.all([
        fetchJson<ReportResponse>('/api/integrity/report', { cache: 'no-store', signal }),
        fetchJson<StoresResponse>('/api/integrity/stores', { cache: 'no-store', signal }),
        fetchJson<TopCategoriesResponse>('/api/integrity/top-categories', { cache: 'no-store', signal }),
      ]);
      if (signal?.aborted) return;
      if (!rpt.ok) throw new Error(`integrity report: HTTP ${rpt.status}`);
      if (!st.ok) throw new Error(`integrity stores: HTTP ${st.status}`);
      if (!tc.ok) throw new Error(`integrity top-categories: HTTP ${tc.status}`);
      setReport(rpt.data);
      setStores(st.data.stores ?? []);
      setTopCats(tc.data);
    } catch (e) {
      if (isAbortError(e) || signal?.aborted) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void loadAll(ac.signal);
    return () => ac.abort();
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
      setRenameMsg(`תצוגה מקדימה: ${JSON.stringify(p.would_update)}`);
      return;
    }
    if (p.rows_updated) {
      setRenameMsg(`עודכן: ${JSON.stringify(p.rows_updated)}`);
    } else {
      setRenameMsg(he.integrity.saved);
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
      setTxnMsg(`מולאו ${p.forward_filled_uncategorized} שורות ללא סיווג עבור ${storeName}.`);
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
    setTxnMsg(
      excluded_from_calculations === 1
        ? `שורה ${rowId} הוחרגה מחישובים.`
        : `שורה ${rowId} הוחזרה לחישובים.`,
    );
    void loadAll();
  };

  const onQuickExclude = async (excluded_from_calculations: 0 | 1) => {
    const idTrim = quickId.trim();
    const fpTrim = quickFingerprint.trim();
    setTxnMsg(null);
    if (!idTrim && !fpTrim) {
      setTxnMsg(he.integrity.enterIdOrFp);
      return;
    }
    setTxnBusy(true);
    const body: Record<string, unknown> = { patch: { excluded_from_calculations } };
    if (idTrim) {
      const n = parseInt(idTrim, 10);
      if (!Number.isFinite(n)) {
        setTxnBusy(false);
        setTxnMsg(he.integrity.rowIdMustBeNumber);
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
    const label = rid != null ? `שורה ${rid}` : 'תנועה';
    setTxnMsg(
      excluded_from_calculations === 1
        ? `${label} הוחרגה מחישובים.`
        : `${label} הוחזרה לחישובים.`,
    );
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
    <PageShell
      title={he.integrity.title}
      sub={he.integrity.sub}
      width="full"
      className="intg-page"
      actions={
        <div className="intg-toolbar">
          <Link className="intg-link" to="/categorize">
            {he.integrity.categorizeQueue}
          </Link>
          <button type="button" className="intg-btn" onClick={() => void loadAll()} disabled={loading}>
            {loading ? he.integrity.loading : he.integrity.refresh}
          </button>
        </div>
      }
    >
      {err ? <div className="intg-error">{err}</div> : null}
      {!report?.ledger_exists ? <p className="intg-muted">{he.integrity.noLedger}</p> : null}
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
        <h2 className="intg-card-title">{he.integrity.excludeAny}</h2>
        <p className="intg-hint">{he.integrity.excludeHint}</p>
        <div className="intg-rename-row">
          <label className="intg-label">
            {he.integrity.rowId}
            <input
              className="intg-input"
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={quickId}
              onChange={(e) => setQuickId(e.target.value)}
              placeholder="1842"
            />
          </label>
          <label className="intg-label">
            {he.integrity.fingerprint}
            <input
              className="intg-input"
              value={quickFingerprint}
              onChange={(e) => setQuickFingerprint(e.target.value)}
              placeholder="fingerprint"
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
            {he.integrity.excludeFromCalcs}
          </button>
          <button
            type="button"
            className="intg-btn intg-btn--secondary"
            disabled={txnBusy || !report?.ledger_exists}
            onClick={() => void onQuickExclude(0)}
          >
            {he.integrity.includeAgain}
          </button>
        </div>
      </section>

      <section className="intg-card">
        <h2 className="intg-card-title">{he.integrity.renameCategory}</h2>
        <p className="intg-hint">{he.integrity.renameHint}</p>
        <div className="intg-rename-row">
          <label className="intg-label">
            {he.integrity.from}
            <input
              className="intg-input"
              value={renameFrom}
              onChange={(e) => setRenameFrom(e.target.value)}
            />
          </label>
          <label className="intg-label">
            {he.integrity.to}
            <input
              className="intg-input"
              value={renameTo}
              onChange={(e) => setRenameTo(e.target.value)}
            />
          </label>
        </div>
        <div className="intg-rename-actions">
          <button
            type="button"
            className="intg-btn intg-btn--secondary"
            disabled={renameBusy}
            onClick={() => void onRename(true)}
          >
            {he.integrity.preview}
          </button>
          <button
            type="button"
            className="intg-btn"
            disabled={renameBusy}
            onClick={() => void onRename(false)}
          >
            {he.integrity.apply}
          </button>
        </div>
        {renameMsg ? <p className="intg-msg">{renameMsg}</p> : null}
      </section>

      <section className="intg-card">
        <h2 className="intg-card-title">{he.integrity.stores}</h2>
        <p className="intg-hint">{he.integrity.storesHint}</p>
        {stores && stores.length === 0 ? <p className="intg-muted">{he.integrity.noStores}</p> : null}
        {stores && stores.length > 0 ? (
          <div className="intg-table-wrap">
            <table className="intg-table">
              <thead>
                <tr>
                  <th>{he.integrity.store}</th>
                  <th>{he.integrity.mode}</th>
                  <th>{he.integrity.catCount}</th>
                  <th>{he.integrity.categories}</th>
                  <th>{he.integrity.actions}</th>
                </tr>
              </thead>
              <tbody>
                {stores.map((s) => (
                  <tr key={s.store_name}>
                    <td>{s.store_name}</td>
                    <td>{s.is_static === 1 ? he.integrity.static : he.integrity.dynamic}</td>
                    <td>{s.category_count}</td>
                    <td className="intg-cats">{s.categories.length ? s.categories.join(', ') : '—'}</td>
                    <td>
                      {s.is_static === 1 ? (
                        <button
                          type="button"
                          className="intg-btn intg-btn--small"
                          onClick={() => void onSetStatic(s.store_name, 0)}
                        >
                          {he.integrity.setDynamic}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="intg-btn intg-btn--small"
                          onClick={() => void onSetStatic(s.store_name, 1)}
                        >
                          {he.integrity.setStatic}
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
        <h2 className="intg-card-title">{he.integrity.checks}</h2>
        {sections.map((sec) => {
          const open = !!expanded[sec.id];
          const sev = severityClass[sec.severity] ?? 'intg-sev--info';
          const cols =
            sec.rows.length > 0
              ? Array.from(new Set(sec.rows.flatMap((r) => Object.keys(r)))).filter(
                  (k) =>
                    k !== 'fingerprint' ||
                    sec.id === 'null_fingerprint' ||
                    sec.id === 'excluded_transactions',
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
                <span className="intg-chevron">{open ? '▼' : '◀'}</span>
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
                        {canPatchRows ? <th>{he.integrity.calculations}</th> : null}
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
                                  {he.integrity.include}
                                </button>
                              ) : rowTxnId(row) !== null && sec.id !== 'excluded_transactions' ? (
                                <button
                                  type="button"
                                  className="intg-btn intg-btn--small"
                                  disabled={txnBusy}
                                  onClick={() => void onPatchExcluded(rowTxnId(row)!, 1)}
                                >
                                  {he.integrity.exclude}
                                </button>
                              ) : null}
                            </td>
                          ) : null}
                          <td className="intg-links">
                            {sec.id === 'rare_categories' && typeof row.category === 'string' ? (
                              <Link to={heatmapDetailCategory('expense', row.category)}>
                                {he.nav.heatmap}
                              </Link>
                            ) : null}
                            {sec.id === 'store_category_orphans' && typeof row.category === 'string' ? (
                              <Link to={heatmapDetailCategory('expense', row.category)}>
                                {he.nav.heatmap}
                              </Link>
                            ) : null}
                            {rowTxnLink(row) ? (
                              <Link to={rowTxnLink(row) ?? '#'}>{he.integrity.detail}</Link>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {open && sec.rows.length === 0 ? (
                <p className="intg-muted">{he.integrity.noSampleRows}</p>
              ) : null}
            </div>
          );
        })}
      </section>
    </PageShell>
  );
}
