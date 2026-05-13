import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { fetchJson, formatMoney, formatPct } from '../lib/api';

import './Portfolio.css';

/** Golden-angle hue steps keep neighbors well separated as `n` grows (dark-UI tuned S/L). */
const GOLDEN_ANGLE = 137.508;

function hslToHex(h: number, s: number, l: number): string {
  const hh = ((h % 360) + 360) % 360;
  const sat = s / 100;
  const light = l / 100;
  const c = (1 - Math.abs(2 * light - 1)) * sat;
  const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
  const m = light - c / 2;
  let rp = 0;
  let gp = 0;
  let bp = 0;
  if (hh < 60) {
    rp = c;
    gp = x;
  } else if (hh < 120) {
    rp = x;
    gp = c;
  } else if (hh < 180) {
    gp = c;
    bp = x;
  } else if (hh < 240) {
    gp = x;
    bp = c;
  } else if (hh < 300) {
    rp = x;
    bp = c;
  } else {
    rp = c;
    bp = x;
  }
  const byte = (n: number) => Math.round(255 * (n + m));
  return `#${[rp, gp, bp].map((n) => byte(n).toString(16).padStart(2, '0')).join('')}`;
}

function generateDistinctColors(count: number): string[] {
  if (count <= 0) return [];
  const out: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const hue = (i * GOLDEN_ANGLE) % 360;
    out.push(hslToHex(hue, 74, 56));
  }
  return out;
}

/** One golden-angle hue per visible series (`visibleSeries` is sorted). Lines keep distinct strokes; crossings never share a color. */
function strokeByVisibleSeries(visibleSeries: readonly string[]): Map<string, string> {
  if (visibleSeries.length === 0) return new Map();
  const palette = generateDistinctColors(visibleSeries.length);
  const out = new Map<string, string>();
  visibleSeries.forEach((sid, i) => {
    out.set(sid, palette[i]!);
  });
  return out;
}

type Instrument = {
  series_id: string;
  portfolio_account: string;
  security_number: string;
  security_name: string | null;
  label: string;
  first_seen: string | null;
  last_seen: string | null;
  latest_value_ils: number | null;
};

/** Position still present on the latest snapshot date in the ledger (`meta.max_date`). */
function isActiveOnLatestSnapshot(
  i: Instrument,
  ledgerMaxDate: string | null | undefined,
): boolean {
  return Boolean(ledgerMaxDate && i.last_seen && i.last_seen === ledgerMaxDate);
}

function defaultSelectedSeriesIds(
  instruments: readonly Instrument[],
  ledgerMaxDate: string | null | undefined,
): string[] {
  const active = instruments.filter((i) => isActiveOnLatestSnapshot(i, ledgerMaxDate));
  if (active.length > 0) return active.map((i) => i.series_id);
  return instruments.map((i) => i.series_id);
}

type PortfolioMeta = {
  ok?: boolean;
  ledger_exists?: boolean;
  min_date?: string | null;
  max_date?: string | null;
  row_count?: number;
  portfolio_accounts?: string[];
  instruments?: Instrument[];
  metrics?: string[];
  default_metric?: string;
};

type TimeseriesPoint = {
  snapshot_date: string;
  series_id: string;
  value: number | null;
  /** Units held on this snapshot; used to stop forward-fill after a full exit (qty <= 0). */
  quantity?: number | null;
  label: string;
};

type TimeseriesPayload = {
  ok?: boolean;
  ledger_exists?: boolean;
  metric?: string;
  points?: TimeseriesPoint[];
};

const METRIC_LABELS: Record<string, string> = {
  value_ils: 'Value (₪)',
  quantity: 'Quantity',
  last_price: 'Last price',
  avg_purchase_price: 'Avg purchase price',
  profit_ils: 'Profit (₪)',
  basis_price: 'Basis price',
  daily_change_pct: 'Daily change %',
  profit_pct: 'Profit %',
  pct_of_portfolio: '% of portfolio',
};

function formatMetricValue(metric: string, v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (metric === 'daily_change_pct' || metric === 'profit_pct' || metric === 'pct_of_portfolio') {
    return formatPct(v);
  }
  if (
    metric === 'quantity' ||
    metric === 'last_price' ||
    metric === 'avg_purchase_price' ||
    metric === 'basis_price'
  ) {
    return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return formatMoney(v);
}

type ObsCell = { value: number | null; quantity: number | null };

/** Do not forward-fill across longer calendar gaps (likely sold out / re-entered later without a row). */
const MAX_FORWARD_FILL_GAP_DAYS = 30;

function dayDiffIso(a: string, b: string): number {
  const t0 = Date.parse(`${a}T12:00:00`);
  const t1 = Date.parse(`${b}T12:00:00`);
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return Number.POSITIVE_INFINITY;
  return Math.abs(t1 - t0) / 86_400_000;
}

/** Largest observation date <= d (ISO YYYY-MM-DD). */
function floorObsLe(obsDates: readonly string[], d: string): string | undefined {
  let lo = 0;
  let hi = obsDates.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (obsDates[mid] <= d) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans >= 0 ? obsDates[ans] : undefined;
}

/** Smallest observation date > d. */
function ceilObsGt(obsDates: readonly string[], d: string): string | undefined {
  let lo = 0;
  let hi = obsDates.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (obsDates[mid] > d) {
      ans = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return ans >= 0 ? obsDates[ans] : undefined;
}

function pivotForChart(
  points: TimeseriesPoint[],
  seriesIds: string[],
): Record<string, string | number | null | undefined>[] {
  const want = new Set(seriesIds);
  const obsByDate = new Map<string, Map<string, ObsCell>>();
  const obsDateSets = new Map<string, Set<string>>();

  for (const p of points) {
    if (!want.has(p.series_id)) continue;
    let bySid = obsByDate.get(p.snapshot_date);
    if (!bySid) {
      bySid = new Map();
      obsByDate.set(p.snapshot_date, bySid);
    }
    const qRaw = p.quantity;
    const qParsed =
      qRaw != null && typeof qRaw === 'number' && Number.isFinite(qRaw) ? qRaw : null;
    bySid.set(p.series_id, { value: p.value, quantity: qParsed });

    let ds = obsDateSets.get(p.series_id);
    if (!ds) {
      ds = new Set();
      obsDateSets.set(p.series_id, ds);
    }
    ds.add(p.snapshot_date);
  }

  const obsDatesBySid = new Map<string, string[]>();
  for (const sid of seriesIds) {
    const ds = obsDateSets.get(sid);
    obsDatesBySid.set(sid, ds ? [...ds].sort() : []);
  }

  const dates = [...obsByDate.keys()].sort();
  const rows: Record<string, string | number | null | undefined>[] = dates.map((d) => ({
    date: d,
  }));

  for (const sid of seriesIds) {
    const obsDates = obsDatesBySid.get(sid) ?? [];
    let lastV: number | undefined;
    let open = false;

    for (let i = 0; i < dates.length; i += 1) {
      const d = dates[i];
      const row = rows[i];
      const obs = obsByDate.get(d)?.get(sid);

      if (obs) {
        const q = obs.quantity;
        const vNum =
          typeof obs.value === 'number' && Number.isFinite(obs.value) ? obs.value : null;

        if (q !== null && q <= 0) {
          row[sid] = vNum;
          lastV = undefined;
          open = false;
        } else {
          open = true;
          if (vNum !== null) {
            lastV = vNum;
            row[sid] = vNum;
          } else {
            row[sid] = lastV !== undefined ? lastV : null;
          }
        }
      } else if (open && lastV !== undefined && obsDates.length > 0) {
        const prevD = floorObsLe(obsDates, d);
        if (prevD === undefined) {
          continue;
        }
        const nextD = ceilObsGt(obsDates, d);
        if (nextD !== undefined) {
          if (d > prevD && d < nextD && dayDiffIso(prevD, nextD) > MAX_FORWARD_FILL_GAP_DAYS) {
            continue;
          }
        } else if (d > prevD) {
          continue;
        }
        row[sid] = lastV;
      }
    }
  }

  return rows;
}

export default function Portfolio() {
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [account, setAccount] = useState('');
  const [metric, setMetric] = useState('value_ils');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [seeded, setSeeded] = useState(false);
  const prevAccountKey = useRef<string | null>(null);

  const metaQuery = useQuery({
    queryKey: ['portfolio-meta'] as const,
    queryFn: async (): Promise<PortfolioMeta> => {
      const r = await fetchJson<PortfolioMeta>('/api/portfolio/meta', { cache: 'no-store' });
      if (!r.ok) throw new Error(`portfolio meta: HTTP ${r.status}`);
      return r.data;
    },
    staleTime: 60_000,
  });

  const meta = metaQuery.data;
  const instruments = meta?.instruments ?? [];

  const listInstruments = useMemo(() => {
    if (!account) return instruments;
    return instruments.filter((i) => i.portfolio_account === account);
  }, [instruments, account]);

  const scopeIds = useMemo(() => listInstruments.map((i) => i.series_id), [listInstruments]);

  useEffect(() => {
    if (!meta?.ledger_exists || seeded) return;
    setFrom(meta.min_date ?? '');
    setTo(meta.max_date ?? '');
    setMetric(meta.default_metric ?? 'value_ils');
    setAccount('');
    setSelected(new Set(defaultSelectedSeriesIds(meta.instruments ?? [], meta.max_date)));
    setSeeded(true);
  }, [meta, seeded]);

  useEffect(() => {
    if (!seeded || !meta?.ledger_exists) return;
    const key = account || '';
    if (prevAccountKey.current === key) return;
    prevAccountKey.current = key;
    const vis = key
      ? (meta.instruments ?? []).filter((i) => i.portfolio_account === key)
      : meta.instruments ?? [];
    setSelected(new Set(defaultSelectedSeriesIds(vis, meta.max_date)));
  }, [account, seeded, meta?.ledger_exists, meta?.instruments, meta?.max_date]);

  const labelBySeries = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of instruments) {
      const multi =
        (meta?.portfolio_accounts?.length ?? 0) > 1
          ? `${i.label} (${i.portfolio_account})`
          : i.label;
      m.set(i.series_id, multi);
    }
    return m;
  }, [instruments, meta?.portfolio_accounts?.length]);

  const seriesParam = useMemo(() => {
    if (!scopeIds.length) return null;
    const scopeSet = new Set(scopeIds);
    const inScope = [...selected].filter((id) => scopeSet.has(id));
    if (inScope.length === 0) return null;
    if (inScope.length === scopeIds.length) return undefined;
    return inScope;
  }, [scopeIds, selected]);

  const seriesQueryKey =
    seriesParam === undefined ? 'all' : seriesParam === null ? 'none' : seriesParam.join('\n');

  const timeseriesQuery = useQuery({
    queryKey: ['portfolio-timeseries', from, to, account, metric, seriesQueryKey] as const,
    queryFn: async (): Promise<TimeseriesPayload> => {
      const p = new URLSearchParams();
      if (from) p.set('from', from);
      if (to) p.set('to', to);
      if (account) p.set('account', account);
      if (metric) p.set('metric', metric);
      if (Array.isArray(seriesParam)) {
        for (const id of seriesParam) p.append('series', id);
      }
      const r = await fetchJson<TimeseriesPayload>(`/api/portfolio/timeseries?${p}`, {
        cache: 'no-store',
      });
      if (!r.ok) throw new Error(`timeseries: HTTP ${r.status}`);
      return r.data;
    },
    enabled: Boolean(
      meta?.ledger_exists && seeded && metaQuery.isSuccess && seriesParam !== null,
    ),
    staleTime: 30_000,
  });

  const ts = timeseriesQuery.data;
  const activeMetric = ts?.metric ?? metric;
  const chartRows = useMemo(() => {
    const pts = ts?.points ?? [];
    const ids = [...selected].filter((id) => scopeIds.includes(id));
    return pivotForChart(pts, ids);
  }, [ts?.points, selected, scopeIds]);

  const visibleSeries = useMemo(
    () => [...selected].filter((id) => scopeIds.includes(id)).sort(),
    [selected, scopeIds],
  );

  const strokeBySeries = useMemo(
    () => strokeByVisibleSeries(visibleSeries),
    [visibleSeries],
  );

  const toggleSeries = useCallback((sid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(scopeIds));
  }, [scopeIds]);

  const selectNone = useCallback(() => {
    setSelected(new Set());
  }, []);

  return (
    <div className="portfolio-page">
      <header className="portfolio-header">
        <h1>Portfolio</h1>
        <p className="portfolio-header__sub">
          Securities snapshots from the ledger (<code>trade_portfolio_position</code>). One line per
          position over snapshot dates.
        </p>
      </header>

      {metaQuery.isError && (
        <p className="portfolio-msg portfolio-msg--err">Could not load portfolio metadata.</p>
      )}

      {meta && !meta.ledger_exists && (
        <p className="portfolio-msg">No ledger database found yet. Run the pipeline import first.</p>
      )}

      {meta?.ledger_exists && (meta.row_count ?? 0) === 0 && (
        <p className="portfolio-msg">
          No trade-portfolio rows in the ledger. Import an אחזקות export from the Pipeline page.
        </p>
      )}

      {meta?.ledger_exists && (meta.row_count ?? 0) > 0 && (
        <>
          <section className="portfolio-filters" aria-label="Chart filters">
            <div className="portfolio-filters__row">
              <label className="portfolio-field">
                <span>From</span>
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <label className="portfolio-field">
                <span>To</span>
                <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </label>
              <label className="portfolio-field">
                <span>Account</span>
                <select value={account} onChange={(e) => setAccount(e.target.value)}>
                  <option value="">All accounts</option>
                  {(meta.portfolio_accounts ?? []).map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
              <label className="portfolio-field">
                <span>Metric</span>
                <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                  {(meta.metrics ?? []).map((m) => (
                    <option key={m} value={m}>
                      {METRIC_LABELS[m] ?? m}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="portfolio-securities">
              <div className="portfolio-securities__head">
                <span>Securities</span>
                <button type="button" className="portfolio-linkish" onClick={selectAll}>
                  All
                </button>
                <button type="button" className="portfolio-linkish" onClick={selectNone}>
                  None
                </button>
              </div>
              <ul className="portfolio-securities__list">
                {listInstruments.map((i) => (
                  <li key={i.series_id}>
                    <label className="portfolio-cb">
                      <input
                        type="checkbox"
                        checked={selected.has(i.series_id)}
                        onChange={() => toggleSeries(i.series_id)}
                      />
                      <span>{i.label}</span>
                      <span className="portfolio-cb__meta">
                        {i.first_seen} → {i.last_seen}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          {timeseriesQuery.isError && (
            <p className="portfolio-msg portfolio-msg--err">Failed to load chart data.</p>
          )}

          {selected.size === 0 && (
            <p className="portfolio-msg">Select at least one security to plot.</p>
          )}

          {selected.size > 0 && (
            <div className="portfolio-chart-wrap">
              <ResponsiveContainer width="100%" height={560}>
                <LineChart data={chartRows} margin={{ top: 8, right: 24, left: 8, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.2)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) =>
                      typeof v === 'number'
                        ? activeMetric.includes('pct')
                          ? `${(v * 100).toFixed(1)}%`
                          : v.toLocaleString(undefined, { maximumFractionDigits: 2 })
                        : String(v)
                    }
                  />
                  <Tooltip
                    formatter={(value, name) => {
                      const n =
                        typeof value === 'number'
                          ? value
                          : value != null && value !== ''
                            ? Number(value)
                            : NaN;
                      return [
                        formatMetricValue(
                          activeMetric,
                          Number.isFinite(n) ? n : undefined,
                        ),
                        labelBySeries.get(String(name)) ?? String(name),
                      ];
                    }}
                    labelFormatter={(l) => `Date: ${l}`}
                  />
                  <Legend
                    wrapperStyle={{ maxHeight: 140, overflowY: 'auto' }}
                    formatter={(value) => labelBySeries.get(String(value)) ?? value}
                  />
                  {visibleSeries.map((sid) => (
                    <Line
                      key={sid}
                      type="monotone"
                      dataKey={sid}
                      name={sid}
                      stroke={strokeBySeries.get(sid) ?? '#888888'}
                      strokeWidth={2}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </div>
  );
}
