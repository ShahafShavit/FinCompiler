import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import PageShell from '../components/PageShell';
import { fetchJson, formatMoney, patchJson, postJson, isAbortError } from '../lib/api';
import { explorePager, exploreQueryLimit } from '../lib/explorePager';
import { he } from '../lib/he';
import { invalidateLedgerViews } from '../lib/invalidateLedger';

import './Explore.css';

const PRESETS_KEY = 'fincompiler.explore.presets';

type SavedPreset = { name: string; query: string };

function loadPresets(): SavedPreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (p): p is SavedPreset =>
        Boolean(p) &&
        typeof p === 'object' &&
        typeof (p as SavedPreset).name === 'string' &&
        typeof (p as SavedPreset).query === 'string',
    );
  } catch {
    return [];
  }
}

function savePresets(list: SavedPreset[]) {
  try {
    localStorage.setItem(PRESETS_KEY, JSON.stringify(list));
  } catch {
    /* ignore */
  }
}

const LTR_COLUMNS = new Set([
  'id',
  'בחובה',
  'בזכות',
  'תאריך',
  'statement_month',
  '4 ספרות',
  'data_updated_at',
  'category_updated_at',
  'ingested_at',
  'excluded_from_calculations',
]);

const MONEY_COLUMNS = new Set(['בחובה', 'בזכות']);

const HIDDEN_COLUMNS = new Set(['id', 'fingerprint']);

const EDITABLE_COLUMNS = new Set(['קטגוריה', 'notes']);

type SearchResponse = {
  ok?: boolean;
  total?: number;
  limit?: number;
  offset?: number;
  columns?: string[];
  rows?: Record<string, unknown>[];
  error?: string;
  message?: string;
};

type Filters = {
  q: string;
  ids: string;
  dateFrom: string;
  dateTo: string;
  amountMin: string;
  amountMax: string;
  direction: 'all' | 'expense' | 'income';
  includeExcluded: boolean;
  offset: number;
};

function parseFilters(sp: URLSearchParams): Filters {
  const directionRaw = sp.get('direction') || 'all';
  const direction =
    directionRaw === 'expense' || directionRaw === 'income' ? directionRaw : 'all';
  const offsetRaw = Number(sp.get('offset') || '0');
  return {
    q: sp.get('q') || '',
    ids: sp.get('ids') || '',
    dateFrom: sp.get('date_from') || '',
    dateTo: sp.get('date_to') || '',
    amountMin: sp.get('amount_min') || '',
    amountMax: sp.get('amount_max') || '',
    direction,
    includeExcluded: sp.get('include_excluded') === '1',
    offset: Number.isFinite(offsetRaw) && offsetRaw > 0 ? Math.floor(offsetRaw) : 0,
  };
}

function filtersToSearchParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.ids.trim()) sp.set('ids', f.ids.trim());
  else if (f.q.trim()) sp.set('q', f.q.trim());
  if (f.dateFrom) sp.set('date_from', f.dateFrom);
  if (f.dateTo) sp.set('date_to', f.dateTo);
  if (f.amountMin.trim()) sp.set('amount_min', f.amountMin.trim());
  if (f.amountMax.trim()) sp.set('amount_max', f.amountMax.trim());
  if (f.direction !== 'all') sp.set('direction', f.direction);
  if (f.includeExcluded) sp.set('include_excluded', '1');
  if (f.offset > 0) sp.set('offset', String(f.offset));
  return sp;
}

function visibleColumns(columns: string[]): string[] {
  return columns.filter((c) => !HIDDEN_COLUMNS.has(c));
}

function columnDir(column: string): 'ltr' | 'auto' {
  return LTR_COLUMNS.has(column) ? 'ltr' : 'auto';
}

function cellDisplay(column: string, v: unknown): string {
  if (v == null || v === '') return '—';
  if (MONEY_COLUMNS.has(column) && typeof v === 'number' && Number.isFinite(v)) {
    return v === 0 ? '—' : formatMoney(v);
  }
  if (column === 'excluded_from_calculations') {
    return v === 1 || v === true || v === '1' ? 'כן' : '—';
  }
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  return String(v);
}

function rowId(row: Record<string, unknown>): number | null {
  const v = row.id;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && /^\d+$/.test(v)) return Number(v);
  return null;
}

function isExcluded(row: Record<string, unknown>): boolean {
  return (
    row.excluded_from_calculations === 1 ||
    row.excluded_from_calculations === true ||
    row.excluded_from_calculations === '1'
  );
}

function normalizeEditPayload(column: string, raw: string): unknown {
  const t = raw.trim();
  if (column === 'notes') return t === '' ? null : raw;
  return t === '' ? null : t;
}

export default function Explore() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);

  const [draftQ, setDraftQ] = useState(filters.q);
  useEffect(() => {
    setDraftQ(filters.q);
  }, [filters.q]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      if (draftQ === filters.q) return;
      setSearchParams(
        filtersToSearchParams({ ...filters, q: draftQ, offset: 0 }),
        { replace: true },
      );
    }, 250);
    return () => window.clearTimeout(t);
  }, [draftQ, filters, setSearchParams]);

  const patchFilters = useCallback(
    (patch: Partial<Filters>, resetOffset = true) => {
      const next: Filters = { ...filters, ...patch };
      if (resetOffset && patch.offset === undefined) next.offset = 0;
      setSearchParams(filtersToSearchParams(next), { replace: true });
    },
    [filters, setSearchParams],
  );

  const apiQuery = useMemo(() => {
    const sp = filtersToSearchParams(filters);
    sp.set('limit', String(exploreQueryLimit()));
    if (!sp.has('offset')) sp.set('offset', '0');
    return sp.toString();
  }, [filters]);

  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rowErr, setRowErr] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [bulkCategory, setBulkCategory] = useState('');
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [presets, setPresets] = useState<SavedPreset[]>(() => loadPresets());
  const [presetName, setPresetName] = useState('');

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetchJson<SearchResponse>(`/api/ledger/search?${apiQuery}`, {
        cache: 'no-store',
        signal,
      });
      if (signal?.aborted) return;
      if (!r.ok || r.data.ok === false) {
        setErr(r.data.message || r.data.error || he.explore.error);
        setData(null);
      } else {
        setData(r.data);
      }
    } catch (e) {
      if (isAbortError(e) || signal?.aborted) return;
      setErr(he.explore.error);
      setData(null);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [apiQuery]);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  useEffect(() => {
    setSelected(new Set());
    setBulkMsg(null);
  }, [apiQuery]);

  const saveCell = async (row: Record<string, unknown>, column: string, raw: string) => {
    const id = rowId(row);
    if (id == null) return;
    const normalized = normalizeEditPayload(column, raw);
    const prev = row[column];
    const prevStr = prev == null ? '' : String(prev);
    const nextStr = normalized == null ? '' : String(normalized);
    if (prevStr.trim() === nextStr.trim()) {
      setRowErr((m) => {
        const n = { ...m };
        delete n[id];
        return n;
      });
      return;
    }
    const r = await patchJson<{ ok?: boolean; error?: string; message?: string }>(
      '/heatmap/api/transaction',
      { id, patch: { [column]: normalized } },
    );
    if (!r.ok || r.data.ok === false) {
      setRowErr((m) => ({
        ...m,
        [id]: r.data.message || r.data.error || he.explore.saveFailed,
      }));
      return;
    }
    setRowErr((m) => {
      const n = { ...m };
      delete n[id];
      return n;
    });
    setData((prevData) => {
      if (!prevData?.rows) return prevData;
      return {
        ...prevData,
        rows: prevData.rows.map((rw) =>
          rowId(rw) === id ? { ...rw, [column]: normalized } : rw,
        ),
      };
    });
    invalidateLedgerViews(queryClient);
  };

  const toggleExclude = async (row: Record<string, unknown>) => {
    const id = rowId(row);
    if (id == null) return;
    const next: 0 | 1 = isExcluded(row) ? 0 : 1;
    const r = await patchJson<{ ok?: boolean; error?: string; message?: string }>(
      '/heatmap/api/transaction',
      { id, patch: { excluded_from_calculations: next } },
    );
    if (!r.ok || r.data.ok === false) {
      setRowErr((m) => ({
        ...m,
        [id]: r.data.message || r.data.error || he.explore.saveFailed,
      }));
      return;
    }
    setData((prevData) => {
      if (!prevData?.rows) return prevData;
      return {
        ...prevData,
        rows: prevData.rows.map((rw) =>
          rowId(rw) === id ? { ...rw, excluded_from_calculations: next } : rw,
        ),
      };
    });
    invalidateLedgerViews(queryClient);
  };

  const runBulkPatch = async (patch: Record<string, unknown>) => {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBulkBusy(true);
    setBulkMsg(null);
    try {
      const r = await postJson<{
        ok?: boolean;
        updated?: number;
        errors?: unknown[];
        message?: string;
        error?: string;
      }>('/api/ledger/bulk-patch', { ids, patch });
      if (!r.ok && !(r.status === 207 && (r.data.updated ?? 0) > 0)) {
        setBulkMsg(r.data.message || r.data.error || he.explore.bulkFailed);
        return;
      }
      setBulkMsg(`${he.explore.bulkOk}: ${r.data.updated ?? 0}`);
      setSelected(new Set());
      invalidateLedgerViews(queryClient);
      await load();
    } catch {
      setBulkMsg(he.explore.bulkFailed);
    } finally {
      setBulkBusy(false);
    }
  };

  const rows = data?.rows || [];
  const total = data?.total ?? 0;
  const offset = filters.offset;
  const cols = visibleColumns(data?.columns || []);
  const pager = explorePager(offset, total, data?.limit ?? exploreQueryLimit());
  const { page, pageCount, canPrev, canNext, pageSize } = pager;

  const pageIds = useMemo(() => {
    const out: number[] = [];
    for (const row of rows) {
      const id = rowId(row);
      if (id != null) out.push(id);
    }
    return out;
  }, [rows]);

  const allPageSelected =
    pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectPage = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of pageIds) next.add(id);
      return next;
    });
  };

  const clearSelection = () => setSelected(new Set());

  const filterBar = (
    <div className="explore-toolbar">
      <label className="explore-field explore-field--grow">
        <span>{he.explore.search}</span>
        <input
          type="search"
          value={draftQ}
          placeholder={he.explore.searchPlaceholder}
          onChange={(e) => setDraftQ(e.target.value)}
          disabled={Boolean(filters.ids.trim())}
        />
      </label>
      {filters.ids.trim() ? (
        <button
          type="button"
          className="secondary"
          onClick={() => patchFilters({ ids: '', q: '', offset: 0 })}
        >
          {he.explore.clearIds}
        </button>
      ) : null}
      <label className="explore-field">
        <span>{he.explore.from}</span>
        <input
          type="date"
          value={filters.dateFrom}
          onChange={(e) => patchFilters({ dateFrom: e.target.value })}
        />
      </label>
      <label className="explore-field">
        <span>{he.explore.to}</span>
        <input
          type="date"
          value={filters.dateTo}
          onChange={(e) => patchFilters({ dateTo: e.target.value })}
        />
      </label>
      <label className="explore-field">
        <span>{he.explore.amountMin}</span>
        <input
          type="text"
          inputMode="decimal"
          dir="ltr"
          value={filters.amountMin}
          onChange={(e) => patchFilters({ amountMin: e.target.value })}
        />
      </label>
      <label className="explore-field">
        <span>{he.explore.amountMax}</span>
        <input
          type="text"
          inputMode="decimal"
          dir="ltr"
          value={filters.amountMax}
          onChange={(e) => patchFilters({ amountMax: e.target.value })}
        />
      </label>
      <label className="explore-field">
        <span>{he.explore.direction}</span>
        <select
          value={filters.direction}
          onChange={(e) =>
            patchFilters({
              direction: e.target.value as Filters['direction'],
            })
          }
        >
          <option value="all">{he.explore.directionAll}</option>
          <option value="expense">{he.explore.directionExpense}</option>
          <option value="income">{he.explore.directionIncome}</option>
        </select>
      </label>
      <label className="explore-check">
        <input
          type="checkbox"
          checked={filters.includeExcluded}
          onChange={(e) => patchFilters({ includeExcluded: e.target.checked })}
        />
        <span>{he.explore.includeExcluded}</span>
      </label>
    </div>
  );

  return (
    <PageShell
      title={he.explore.title}
      sub={he.explore.sub}
      width="full"
      className="explore-page"
      actions={filterBar}
    >
      <p className="explore-edit-hint">{he.explore.editHint}</p>
      {filters.ids.trim() ? (
        <p className="explore-edit-hint">{he.explore.idsMode}</p>
      ) : null}

      <div className="explore-presets">
        <input
          type="text"
          className="explore-preset-name"
          dir="auto"
          placeholder={he.explore.presetName}
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
        />
        <button
          type="button"
          className="secondary"
          disabled={!presetName.trim()}
          onClick={() => {
            const name = presetName.trim();
            const query = filtersToSearchParams(filters).toString();
            const next = [...presets.filter((p) => p.name !== name), { name, query }];
            setPresets(next);
            savePresets(next);
            setPresetName('');
          }}
        >
          {he.explore.savePreset}
        </button>
        <select
          value=""
          onChange={(e) => {
            const name = e.target.value;
            if (!name) return;
            const hit = presets.find((p) => p.name === name);
            if (!hit) return;
            setSearchParams(new URLSearchParams(hit.query), { replace: true });
          }}
          aria-label={he.explore.loadPreset}
        >
          <option value="">{presets.length ? he.explore.loadPreset : he.explore.noPresets}</option>
          {presets.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="secondary"
          disabled={!presets.length || !presetName.trim()}
          onClick={() => {
            const name = presetName.trim();
            const next = presets.filter((p) => p.name !== name);
            setPresets(next);
            savePresets(next);
            setPresetName('');
          }}
        >
          {he.explore.deletePreset}
        </button>
      </div>

      {err ? <p className="explore-msg explore-msg--err">{err}</p> : null}

      {loading && !data ? (
        <p className="explore-msg">{he.explore.loading}</p>
      ) : !err && total === 0 ? (
        <p className="explore-msg">{he.explore.empty}</p>
      ) : !err && rows.length > 0 ? (
        <>
          <div className="explore-pager">
            <span className="explore-count" dir="ltr">
              {he.explore.count}: {total.toLocaleString('he-IL')} · {he.explore.page} {page}/
              {pageCount}
            </span>
            <div className="explore-pager-btns">
              <button
                type="button"
                className="secondary"
                disabled={!canPrev || loading}
                onClick={() => patchFilters({ offset: Math.max(0, offset - pageSize) }, false)}
              >
                {he.explore.prev}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={!canNext || loading}
                onClick={() => patchFilters({ offset: offset + pageSize }, false)}
              >
                {he.explore.next}
              </button>
            </div>
          </div>

          <div className="explore-bulk-bar">
            <button
              type="button"
              className="secondary"
              disabled={pageIds.length === 0 || allPageSelected}
              onClick={selectPage}
            >
              {he.explore.selectPage}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={selected.size === 0}
              onClick={clearSelection}
            >
              {he.explore.clearSelection}
            </button>
            <span className="explore-count" dir="ltr">
              {he.explore.selectedCount}: {selected.size}
            </span>
            <input
              type="text"
              className="explore-bulk-cat"
              dir="auto"
              placeholder={he.explore.bulkCategoryPlaceholder}
              value={bulkCategory}
              onChange={(e) => setBulkCategory(e.target.value)}
              disabled={selected.size === 0 || bulkBusy}
            />
            <button
              type="button"
              disabled={selected.size === 0 || !bulkCategory.trim() || bulkBusy}
              onClick={() => void runBulkPatch({ קטגוריה: bulkCategory.trim() })}
            >
              {he.explore.applyCategory}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={selected.size === 0 || bulkBusy}
              onClick={() => void runBulkPatch({ excluded_from_calculations: 1 })}
            >
              {he.explore.applyExclude}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={selected.size === 0 || bulkBusy}
              onClick={() => void runBulkPatch({ excluded_from_calculations: 0 })}
            >
              {he.explore.applyInclude}
            </button>
            {bulkMsg ? <span className="explore-bulk-msg">{bulkMsg}</span> : null}
          </div>

          <div className="explore-table-wrap">
            <table className="explore-table">
              <thead>
                <tr>
                  <th className="explore-col-check" aria-label="select" />
                  {cols.map((c) => (
                    <th key={c} dir={columnDir(c)}>
                      {c}
                    </th>
                  ))}
                  <th>{he.explore.excludeToggle}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => {
                  const rid = rowId(row);
                  const key =
                    rid != null
                      ? String(rid)
                      : `r-${offset + ri}`;
                  const excluded = isExcluded(row);
                  const re = rid != null ? rowErr[rid] : undefined;
                  return (
                    <Fragment key={key}>
                      <tr className={excluded ? 'explore-row--excluded' : undefined}>
                        <td className="explore-col-check">
                          {rid != null ? (
                            <input
                              type="checkbox"
                              checked={selected.has(rid)}
                              onChange={() => toggleSelect(rid)}
                              aria-label={`select ${rid}`}
                            />
                          ) : null}
                        </td>
                        {cols.map((c) => (
                          <td key={c} dir={columnDir(c)}>
                            {rid != null && EDITABLE_COLUMNS.has(c) ? (
                              c === 'notes' ? (
                                <textarea
                                  className="explore-cell-input explore-cell-notes"
                                  dir="auto"
                                  defaultValue={
                                    row[c] == null
                                      ? ''
                                      : typeof row[c] === 'string'
                                        ? row[c]
                                        : String(row[c])
                                  }
                                  key={`${key}-${c}-${String(row[c] ?? '')}`}
                                  onBlur={(e) => void saveCell(row, c, e.target.value)}
                                />
                              ) : (
                                <input
                                  type="text"
                                  className="explore-cell-input"
                                  dir="auto"
                                  defaultValue={
                                    row[c] == null
                                      ? ''
                                      : typeof row[c] === 'string'
                                        ? row[c]
                                        : String(row[c])
                                  }
                                  key={`${key}-${c}-${String(row[c] ?? '')}`}
                                  onBlur={(e) => void saveCell(row, c, e.target.value)}
                                />
                              )
                            ) : (
                              cellDisplay(c, row[c])
                            )}
                          </td>
                        ))}
                        <td>
                          {rid != null ? (
                            <button
                              type="button"
                              className="secondary explore-exclude-btn"
                              onClick={() => void toggleExclude(row)}
                            >
                              {excluded ? he.explore.excludeOn : he.explore.excludeOff}
                            </button>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                      {re ? (
                        <tr className="explore-row-err-tr">
                          <td className="explore-row-err" colSpan={cols.length + 2}>
                            {re}
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </PageShell>
  );
}
