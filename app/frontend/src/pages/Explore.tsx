import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import PageShell from '../components/PageShell';
import { fetchJson, formatMoney } from '../lib/api';
import { he } from '../lib/he';

import './Explore.css';

const PAGE_SIZE = 50;

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
  if (f.q.trim()) sp.set('q', f.q.trim());
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

export default function Explore() {
  const [searchParams, setSearchParams] = useSearchParams();
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
    sp.set('limit', String(PAGE_SIZE));
    if (!sp.has('offset')) sp.set('offset', '0');
    return sp.toString();
  }, [filters]);

  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    void (async () => {
      try {
        const r = await fetchJson<SearchResponse>(`/api/ledger/search?${apiQuery}`, {
          cache: 'no-store',
        });
        if (cancelled) return;
        if (r.status === 503) {
          setData(r.data);
          setErr(he.explore.unavailable);
          return;
        }
        if (!r.ok || !r.data.ok) {
          setData(null);
          setErr(r.data.message || he.explore.error);
          return;
        }
        setData(r.data);
      } catch {
        if (!cancelled) {
          setData(null);
          setErr(he.explore.error);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiQuery]);

  const total = data?.total ?? 0;
  const offset = filters.offset;
  const cols = visibleColumns(data?.columns ?? []);
  const rows = data?.rows ?? [];
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  const filterBar = (
    <div className="explore-toolbar" aria-label={he.explore.filters}>
      <label className="explore-field explore-field--grow">
        <span>{he.explore.search}</span>
        <input
          type="search"
          value={draftQ}
          placeholder={he.explore.searchPlaceholder}
          onChange={(e) => setDraftQ(e.target.value)}
        />
      </label>
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
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0"
          value={filters.amountMin}
          onChange={(e) => patchFilters({ amountMin: e.target.value })}
        />
      </label>
      <label className="explore-field">
        <span>{he.explore.amountMax}</span>
        <input
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0"
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
                onClick={() => patchFilters({ offset: Math.max(0, offset - PAGE_SIZE) }, false)}
              >
                {he.explore.prev}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={!canNext || loading}
                onClick={() => patchFilters({ offset: offset + PAGE_SIZE }, false)}
              >
                {he.explore.next}
              </button>
            </div>
          </div>

          <div className="explore-table-wrap">
            <table className="explore-table">
              <thead>
                <tr>
                  {cols.map((c) => (
                    <th key={c} dir={columnDir(c)}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => {
                  const rid = row.id;
                  const key =
                    typeof rid === 'number' || typeof rid === 'string'
                      ? String(rid)
                      : `r-${offset + ri}`;
                  const excluded =
                    row.excluded_from_calculations === 1 ||
                    row.excluded_from_calculations === true ||
                    row.excluded_from_calculations === '1';
                  return (
                    <tr key={key} className={excluded ? 'explore-row--excluded' : undefined}>
                      {cols.map((c) => (
                        <td key={c} dir={columnDir(c)}>
                          {cellDisplay(c, row[c])}
                        </td>
                      ))}
                    </tr>
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
