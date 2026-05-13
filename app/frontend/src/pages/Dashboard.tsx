import { useMemo, useState, type CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { fetchJson, formatMoney, formatPct } from '../lib/api';
import {
  heatmapDetailCategory,
  heatmapDetailMonth,
  heatmapDetailSourceCategory,
} from '../lib/drilldown';
import type {
  AllocationRow,
  AllocationTimelineRow,
  CashflowRow,
  CategoryPeriodStatRow,
  CategoryPeriodStatsResponse,
  DashboardSummary,
  LedgerMeta,
  MonthBoundsResponse,
  SourceCategoryMatrixResponse,
} from '../lib/dashboardTypes';

import './Dashboard.css';

const DASH_QUERY_GC_MS = 60 * 60 * 1000;

function useLedgerMetaQuery() {
  return useQuery({
    queryKey: ['ledger-meta'] as const,
    queryFn: async (): Promise<LedgerMeta> => {
      const r = await fetchJson<LedgerMeta>('/api/ledger-meta');
      if (!r.ok) throw new Error(`ledger-meta: HTTP ${r.status}`);
      return r.data;
    },
    staleTime: 0,
    gcTime: DASH_QUERY_GC_MS,
    refetchOnWindowFocus: true,
  });
}

function dashboardRevisionFromMeta(meta: LedgerMeta | undefined): number | 'no_file' | null {
  if (!meta?.ok) return null;
  if (!meta.exists) return 'no_file';
  if (meta.mtime_ns == null) return null;
  return meta.mtime_ns;
}

async function fetchDashboardJson<T>(url: string): Promise<T> {
  const r = await fetchJson<T>(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.data;
}

function useDashboardQuery<T>(revision: number | 'no_file' | null, url: string, metaReady: boolean) {
  return useQuery({
    queryKey: ['dashboard', url, revision] as const,
    queryFn: () => fetchDashboardJson<T>(url),
    enabled: metaReady && revision !== null,
    staleTime: Infinity,
    gcTime: DASH_QUERY_GC_MS,
  });
}

const PALETTE = [
  '#4c6ef5',
  '#22b8cf',
  '#82c91e',
  '#fab005',
  '#fa5252',
  '#be4bdb',
  '#15aabf',
  '#9775fa',
  '#ff922b',
  '#37b24d',
];

function hashColor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return PALETTE[h % PALETTE.length];
}

type KPIProps = {
  label: string;
  value: string;
  sub?: string;
  delta?: number | null;
  cta?: { href: string; label: string };
};

function KPI({ label, value, sub, delta, cta }: KPIProps) {
  const dir =
    delta == null
      ? ''
      : delta > 0
        ? 'dash-kpi__delta--up'
        : delta < 0
          ? 'dash-kpi__delta--down'
          : '';
  const deltaText =
    delta == null
      ? null
      : `${delta > 0 ? '▲' : delta < 0 ? '▼' : '·'} ${formatMoney(Math.abs(delta))}`;
  return (
    <div className="dash-kpi">
      <span className="dash-kpi__label">{label}</span>
      <span className="dash-kpi__value">{value}</span>
      {sub ? <span className="dash-kpi__sub">{sub}</span> : null}
      {deltaText ? <span className={`dash-kpi__sub ${dir}`}>{deltaText}</span> : null}
      {cta ? (
        <a className="dash-kpi__cta" href={cta.href}>
          {cta.label} →
        </a>
      ) : null}
    </div>
  );
}

type TooltipPayload = { name?: string; value?: number; color?: string };

function MoneyTooltip({ active, payload, label }: { active?: boolean; payload?: TooltipPayload[]; label?: string }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="dash-tooltip">
      <div style={{ marginBottom: '0.25rem', fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="dash-tooltip__row">
          <span style={{ color: p.color }}>{p.name}</span>
          <span>{formatMoney(p.value ?? 0)}</span>
        </div>
      ))}
    </div>
  );
}

function CashflowMonthTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: string;
  payload?: Array<{ payload?: CashflowRow }>;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="dash-tooltip">
      <div style={{ marginBottom: '0.25rem', fontWeight: 600 }}>{label}</div>
      <div className="dash-tooltip__row">
        <span style={{ color: '#37b24d' }}>Income</span>
        <span>{formatMoney(row.income)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span style={{ color: '#fa5252' }}>Expense</span>
        <span>{formatMoney(row.expense)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span style={{ color: '#a5b4fc' }}>Net</span>
        <span>{formatMoney(row.net)}</span>
      </div>
    </div>
  );
}

function CategoryChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: CategoryPeriodStatRow }>;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="dash-tooltip">
      <div style={{ marginBottom: '0.25rem', fontWeight: 600 }}>{row.category}</div>
      <div className="dash-tooltip__row">
        <span>Income</span>
        <span>{formatMoney(row.income)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>Expense</span>
        <span>{formatMoney(row.expense)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>Net</span>
        <span>{formatMoney(row.net)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>Avg monthly net</span>
        <span>{formatMoney(row.avg_monthly_net)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>Yr proj. net</span>
        <span>{formatMoney(yearlyNetProjectionOf(row))}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>% of period income</span>
        <span>{formatPct(row.pct_of_period_income)}</span>
      </div>
      <div className="dash-tooltip__row">
        <span>% of period expense</span>
        <span>{formatPct(row.pct_of_period_expense)}</span>
      </div>
    </div>
  );
}

function AllocationDonutCard() {
  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const metaReady = meta.isSuccess;
  const { data, error, isPending } = useDashboardQuery<{
    ok: boolean;
    rows: AllocationRow[];
    as_of_date: string | null;
  }>(rev, '/api/dashboard/allocation', metaReady);
  const filtered = useMemo(
    () => (data?.rows ?? []).filter((r) => Math.abs(r.balance_ils) > 0.005),
    [data?.rows],
  );
  return (
    <div className="dash-card dash-card--compact">
      <h3 className="dash-card__title">
        Allocation now{' '}
        {data?.as_of_date ? (
          <span className="dash-card__meta">({data.as_of_date})</span>
        ) : null}
      </h3>
      {isPending ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error.message}</div>
      ) : filtered.length === 0 ? (
        <div className="dash-empty">No holdings snapshot yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <PieChart>
            <Pie
              data={filtered}
              dataKey="balance_ils"
              nameKey="activity_type"
              innerRadius={52}
              outerRadius={88}
              paddingAngle={1}
            >
              {filtered.map((row) => (
                <Cell key={row.activity_type} fill={hashColor(row.activity_type)} />
              ))}
            </Pie>
            <Tooltip content={<MoneyTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.74rem' }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

type AllocPivot = { as_of_date: string } & Record<string, number | string>;

function NetWorthStackedCard() {
  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const metaReady = meta.isSuccess;
  const { data, error, isPending } = useDashboardQuery<{
    ok: boolean;
    rows: AllocationTimelineRow[];
    activity_types: string[];
  }>(rev, '/api/dashboard/allocation-timeline', metaReady);
  const { rows, types } = useMemo(() => {
    const out: AllocPivot[] = [];
    const byDate: Record<string, AllocPivot> = {};
    const types_ = data?.activity_types ?? [];
    for (const r of data?.rows ?? []) {
      let bucket = byDate[r.as_of_date];
      if (!bucket) {
        bucket = { as_of_date: r.as_of_date };
        for (const t of types_) bucket[t] = 0;
        byDate[r.as_of_date] = bucket;
        out.push(bucket);
      }
      bucket[r.activity_type] = r.balance_ils;
    }
    out.sort((a, b) => (a.as_of_date < b.as_of_date ? -1 : 1));
    return { rows: out, types: types_ };
  }, [data]);
  return (
    <div className="dash-card dash-card--compact dash-card--grow">
      <h3 className="dash-card__title">Net worth over time</h3>
      <p className="dash-card__sub">
        Stacked balances by activity type; total height = net worth per snapshot.
      </p>
      {isPending ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error.message}</div>
      ) : rows.length === 0 ? (
        <div className="dash-empty">No holdings snapshots yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={rows} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" />
            <XAxis dataKey="as_of_date" stroke="#aeb4c0" fontSize={11} />
            <YAxis stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
            <Tooltip content={<MoneyTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.72rem' }} />
            {types.map((t) => (
              <Area
                key={t}
                type="monotone"
                dataKey={t}
                stackId="1"
                name={t}
                stroke={hashColor(t)}
                fill={hashColor(t)}
                fillOpacity={0.7}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function CashflowCard() {
  const navigate = useNavigate();
  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const metaReady = meta.isSuccess;
  const cashflowUrl = '/api/dashboard/cashflow-monthly?months=24';
  const { data, error, isPending } = useDashboardQuery<{ ok: boolean; rows: CashflowRow[] }>(
    rev,
    cashflowUrl,
    metaReady,
  );
  const rows = useMemo(
    () =>
      (data?.rows ?? []).map((r) => ({
        ...r,
        expenseNeg: -Math.abs(r.expense),
      })),
    [data?.rows],
  );
  return (
    <div className="dash-card dash-card--chart-click">
      <h3 className="dash-card__title">Monthly cash flow</h3>
      <p className="dash-card__sub">
        Last 24 months · green = income above 0, red = expense below 0 (outflows), blue = net cash flow (income − expense).
        Click a bar or net point to drill.{' '}
        <a className="dash-inline-link" href="/heatmap">
          Category heatmap
        </a>
      </p>
      {isPending ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error.message}</div>
      ) : rows.length === 0 ? (
        <div className="dash-empty">No transactions yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart
            data={rows}
            margin={{ top: 8, right: 10, left: 4, bottom: 0 }}
            barCategoryGap="18%"
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" vertical={false} />
            <ReferenceLine
              yAxisId="left"
              y={0}
              stroke="#e8e8ec"
              strokeWidth={2}
              strokeLinecap="square"
            />
            <XAxis dataKey="month" stroke="#aeb4c0" fontSize={11} />
            <YAxis
              yAxisId="left"
              stroke="#aeb4c0"
              fontSize={11}
              tickFormatter={(v) => formatMoney(v as number, '')}
            />
            <Tooltip content={<CashflowMonthTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
            <Bar
              yAxisId="left"
              dataKey="income"
              name="Income"
              fill="#37b24d"
              maxBarSize={44}
              cursor="pointer"
              onClick={(d: unknown) => {
                const m = (d as CashflowRow)?.month;
                if (m) navigate(heatmapDetailMonth('income', m));
              }}
            />
            <Bar
              yAxisId="left"
              dataKey="expenseNeg"
              name="Expense (outflow)"
              fill="#fa5252"
              maxBarSize={44}
              cursor="pointer"
              onClick={(d: unknown) => {
                const m = (d as CashflowRow)?.month;
                if (m) navigate(heatmapDetailMonth('expense', m));
              }}
            />
            <Line
              yAxisId="left"
              type="monotone"
              dataKey="net"
              name="Net"
              stroke="#a5b4fc"
              strokeWidth={2}
              isAnimationActive={false}
              dot={(dotProps: { cx?: number; cy?: number; payload?: CashflowRow }) => {
                const { cx, cy, payload } = dotProps;
                if (cx == null || cy == null) return null;
                return (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={4}
                    fill="#a5b4fc"
                    stroke="#121316"
                    strokeWidth={1}
                    cursor="pointer"
                    onClick={() => {
                      if (payload?.month) navigate(heatmapDetailMonth('net', payload.month));
                    }}
                  />
                );
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

type CategoryRangeKind = 'preset' | 'year' | 'custom';

const CATEGORY_STATS_ROW_LIMIT = 500;

const CATEGORY_PRESET_OPTIONS: { value: string; label: string }[] = [
  { value: '30d', label: 'Last 30 days' },
  { value: 'ytd', label: 'Year to date' },
  { value: '3m', label: 'Last 3 months' },
  { value: '6m', label: 'Last 6 months' },
  { value: '12m', label: 'Last 12 months' },
  { value: 'all', label: 'All time' },
];

function yearsDescendingFromBounds(minYm: string | null, maxYm: string | null): number[] {
  if (!minYm || !maxYm || minYm.length < 4 || maxYm.length < 4) return [];
  const yLo = Number(minYm.slice(0, 4));
  const yHi = Number(maxYm.slice(0, 4));
  if (!Number.isFinite(yLo) || !Number.isFinite(yHi)) return [];
  const a = Math.min(yLo, yHi);
  const b = Math.max(yLo, yHi);
  const out: number[] = [];
  for (let y = b; y >= a; y -= 1) out.push(y);
  return out;
}

type CatSortKey =
  | 'flow'
  | 'category'
  | 'income'
  | 'expense'
  | 'net'
  | 'avg_monthly_net'
  | 'yearly_net_proj'
  | 'txn_count'
  | 'pct_in'
  | 'pct_ex';

function flowOf(r: CategoryPeriodStatRow): number {
  return r.income + r.expense;
}

/** Frontend-only: linear annualization of window avg monthly net. */
function yearlyNetProjectionOf(r: CategoryPeriodStatRow): number {
  return r.avg_monthly_net * 12;
}

function compareCatRows(a: CategoryPeriodStatRow, b: CategoryPeriodStatRow, key: CatSortKey, dir: 'asc' | 'desc'): number {
  let cmp: number;
  if (key === 'category') {
    cmp = a.category.localeCompare(b.category, undefined, { sensitivity: 'base' });
  } else if (key === 'flow') {
    cmp = flowOf(a) - flowOf(b);
  } else if (key === 'pct_in') {
    cmp = a.pct_of_period_income - b.pct_of_period_income;
  } else if (key === 'pct_ex') {
    cmp = a.pct_of_period_expense - b.pct_of_period_expense;
  } else if (key === 'yearly_net_proj') {
    cmp = yearlyNetProjectionOf(a) - yearlyNetProjectionOf(b);
  } else {
    cmp = a[key] - b[key];
  }
  return dir === 'asc' ? cmp : -cmp;
}

function CategoryOverviewCard() {
  const navigate = useNavigate();
  const [rangeKind, setRangeKind] = useState<CategoryRangeKind>('preset');
  const [presetPeriod, setPresetPeriod] = useState('12m');
  const [calendarYear, setCalendarYear] = useState('');
  const [customStartYm, setCustomStartYm] = useState('');
  const [customEndYm, setCustomEndYm] = useState('');

  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const metaReady = meta.isSuccess;

  const boundsQuery = useDashboardQuery<MonthBoundsResponse>(
    rev,
    '/api/dashboard/month-bounds',
    meta.isSuccess && meta.data.exists === true,
  );
  const yearOptions = useMemo(
    () => yearsDescendingFromBounds(boundsQuery.data?.min_ym ?? null, boundsQuery.data?.max_ym ?? null),
    [boundsQuery.data?.min_ym, boundsQuery.data?.max_ym],
  );

  /** Keeps fetch URL / controlled year select aligned when bounds arrive or state is stale. */
  const effectiveCalendarYear = useMemo(() => {
    if (!yearOptions.length) return '';
    const parsed = Number.parseInt(calendarYear, 10);
    if (Number.isFinite(parsed) && yearOptions.includes(parsed)) return String(parsed);
    return String(yearOptions[0]);
  }, [calendarYear, yearOptions]);

  const onRangeKindChange = (next: CategoryRangeKind) => {
    setRangeKind(next);
    if (
      next === 'year' &&
      boundsQuery.isSuccess &&
      boundsQuery.data &&
      yearOptions.length > 0
    ) {
      const parsed = Number.parseInt(calendarYear, 10);
      const valid = Number.isFinite(parsed) && yearOptions.includes(parsed);
      if (!valid) setCalendarYear(String(yearOptions[0]));
    }
  };

  const catUrl = useMemo(() => {
    const p = new URLSearchParams();
    if (rangeKind === 'preset' && presetPeriod === 'all') {
      p.set('limit', '0');
    } else {
      p.set('limit', String(CATEGORY_STATS_ROW_LIMIT));
    }
    if (rangeKind === 'preset') {
      p.set('period', presetPeriod);
    } else if (rangeKind === 'year') {
      p.set('period', effectiveCalendarYear || '12m');
    } else {
      p.set('period', '12m');
      const a = customStartYm.trim();
      const b = customEndYm.trim();
      if (a && b) {
        p.set('start_ym', a);
        p.set('end_ym', b);
      }
    }
    return `/api/dashboard/category-period-stats?${p.toString()}`;
  }, [rangeKind, presetPeriod, effectiveCalendarYear, customStartYm, customEndYm]);

  const yearPickerBlocked =
    rangeKind === 'year' && boundsQuery.isSuccess && yearOptions.length === 0;

  const yearChosenOk =
    rangeKind !== 'year' ||
    (boundsQuery.isSuccess && yearOptions.length > 0 && effectiveCalendarYear !== '');

  const catQueryReady =
    metaReady &&
    rev !== null &&
    !yearPickerBlocked &&
    yearChosenOk &&
    (rangeKind !== 'custom' || (customStartYm.trim() !== '' && customEndYm.trim() !== ''));

  const { data, error, isPending } = useDashboardQuery<CategoryPeriodStatsResponse>(
    rev,
    catUrl,
    catQueryReady,
  );

  const heatmapYmRange =
    rangeKind === 'custom' && customStartYm.trim() && customEndYm.trim()
      ? { startYm: customStartYm.trim(), endYm: customEndYm.trim() }
      : null;

  function drillCategory(cat: string, reportType: 'net' | 'income' | 'expense') {
    const periodTok =
      rangeKind === 'preset'
        ? presetPeriod
        : rangeKind === 'year'
          ? effectiveCalendarYear || '12m'
          : '12m';
    navigate(heatmapDetailCategory(reportType, cat, periodTok, heatmapYmRange));
  }

  const [view, setView] = useState<'table' | 'chart'>('table');
  const [sort, setSort] = useState<{ key: CatSortKey; dir: 'asc' | 'desc' }>({
    key: 'flow',
    dir: 'desc',
  });

  const sortedRows = useMemo(() => {
    const base = [...(data?.rows ?? [])];
    base.sort((a, b) => compareCatRows(a, b, sort.key, sort.dir));
    return base;
  }, [data?.rows, sort]);

  const chartRows = useMemo(() => [...sortedRows].reverse(), [sortedRows]);

  const cycleSort = (key: CatSortKey) => {
    setSort((s) =>
      s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: key === 'category' ? 'asc' : 'desc' },
    );
  };

  const sortMark = (key: CatSortKey) => (sort.key === key ? (sort.dir === 'desc' ? ' ▼' : ' ▲') : '');

  const windowHint = data?.window_label;

  return (
    <div className="dash-card">
      <div className="dash-cat-head">
        <div className="dash-cat-head__main">
          <h3 className="dash-card__title dash-card__title--inline">Category overview</h3>
          <p className="dash-card__sub dash-card__sub--tight">
            Ranked by income + expense; <strong>Avg/mo net</strong> divides each category&apos;s period net by
            the window month count; <strong>Yr proj.</strong> is that average × 12 (not annualized for partial
            windows).{' '}
            <a className="dash-inline-link" href="/heatmap">
              Full heatmap
            </a>
          </p>
          {windowHint ? (
            <p className="dash-card-meta-line dash-card-meta-line--muted">
              Window: <strong>{windowHint}</strong>
            </p>
          ) : null}
          {data?.period_income_total != null ? (
            <p className="dash-card-meta-line">
              Period totals: <strong>{formatMoney(data.period_income_total)}</strong> income ·{' '}
              <strong>{formatMoney(data.period_expense_total)}</strong> expenses
              {' — '}
              <span className="dash-card-meta-line__hint">
                sums include every included transaction (not only the table below).
              </span>
            </p>
          ) : null}
          {data?.avg_monthly_net != null && data?.period_months != null && data?.period_net_total != null ? (
            <p className="dash-card-meta-line dash-card-meta-line--small">
              Overall avg monthly net:{' '}
              <strong className={data.avg_monthly_net >= 0 ? 'dash-num--in' : 'dash-num--out'}>
                {formatMoney(data.avg_monthly_net)}
              </strong>
              <span className="dash-card-meta-line__hint">
                {' '}
                ({formatMoney(data.period_net_total)} net over {data.period_months}{' '}
                {data.period_months === 1 ? 'month' : 'months'}
                {rangeKind === 'preset' && presetPeriod === '30d' ? '; last 30 days treated as one period' : ''})
              </span>
            </p>
          ) : null}
          {data?.category_bucket_count != null && data.rows?.length ? (
            <p className="dash-card-meta-line dash-card-meta-line--small">
              {data.rows.length < data.category_bucket_count
                ? `Table: showing ${data.rows.length} of ${data.category_bucket_count} categories.${data.limit > 0 ? ` Row limit is ${data.limit}.` : ''} Open heatmap for the full breakdown.`
                : `Table: all ${data.category_bucket_count} categories with activity in this window.`}
            </p>
          ) : null}
        </div>
        <div className="dash-cat-head__aside">
          <div className="dash-cat-period-toolbar">
            <label className="dash-cat-period-wrap" htmlFor="dash-cat-range-kind">
              <span className="dash-cat-period-label">Range</span>
              <select
                id="dash-cat-range-kind"
                className="dash-cat-period-select"
                value={rangeKind}
                onChange={(e) => onRangeKindChange(e.target.value as CategoryRangeKind)}
              >
                <option value="preset">Preset</option>
                <option value="year">Calendar year</option>
                <option value="custom">Custom months</option>
              </select>
            </label>
            {rangeKind === 'preset' ? (
              <label className="dash-cat-period-wrap" htmlFor="dash-cat-period-select">
                <span className="dash-cat-period-label">Window</span>
                <select
                  id="dash-cat-period-select"
                  className="dash-cat-period-select"
                  value={presetPeriod}
                  onChange={(e) => setPresetPeriod(e.target.value)}
                >
                  {CATEGORY_PRESET_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {rangeKind === 'year' ? (
              <label className="dash-cat-period-wrap" htmlFor="dash-cat-year-select">
                <span className="dash-cat-period-label">Year</span>
                <select
                  id="dash-cat-year-select"
                  className="dash-cat-period-select"
                  disabled={!boundsQuery.isSuccess || yearOptions.length === 0}
                  value={effectiveCalendarYear}
                  onChange={(e) => setCalendarYear(e.target.value)}
                >
                  {!boundsQuery.isSuccess ? (
                    <option value="">Loading years…</option>
                  ) : !yearOptions.length ? (
                    <option value="">No data range</option>
                  ) : (
                    yearOptions.map((y) => (
                      <option key={y} value={String(y)}>
                        {y}
                      </option>
                    ))
                  )}
                </select>
              </label>
            ) : null}
            {rangeKind === 'custom' ? (
              <>
                <label className="dash-cat-period-wrap" htmlFor="dash-cat-ym-start">
                  <span className="dash-cat-period-label">From</span>
                  <input
                    id="dash-cat-ym-start"
                    className="dash-cat-period-input-month"
                    type="month"
                    value={customStartYm}
                    onChange={(e) => setCustomStartYm(e.target.value)}
                  />
                </label>
                <label className="dash-cat-period-wrap" htmlFor="dash-cat-ym-end">
                  <span className="dash-cat-period-label">To</span>
                  <input
                    id="dash-cat-ym-end"
                    className="dash-cat-period-input-month"
                    type="month"
                    value={customEndYm}
                    onChange={(e) => setCustomEndYm(e.target.value)}
                  />
                </label>
              </>
            ) : null}
          </div>
          <div className="dash-cat-head__view">
            <div className="dash-seg dash-seg--grow" role="tablist" aria-label="View mode">
              <button
                type="button"
                className="dash-seg__btn"
                data-active={view === 'table'}
                onClick={() => setView('table')}
              >
                Table
              </button>
              <button
                type="button"
                className="dash-seg__btn"
                data-active={view === 'chart'}
                onClick={() => setView('chart')}
              >
                Chart
              </button>
            </div>
          </div>
        </div>
      </div>

      {!catQueryReady ? (
        <div className="dash-empty">
          {yearPickerBlocked
            ? 'No effective months in ledger for a year view.'
            : !metaReady || rev === null
              ? 'Loading…'
              : rangeKind === 'custom'
                ? 'Choose start and end months.'
                : rangeKind === 'year'
                  ? 'Loading year list…'
                  : 'Loading…'}
        </div>
      ) : isPending ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error.message}</div>
      ) : !data?.rows?.length ? (
        <div className="dash-empty">No categorized activity in this period.</div>
      ) : view === 'table' ? (
        <div className="dash-cat-table-wrap">
          <table className="dash-cat-table">
            <thead>
              <tr>
                <th scope="col" className="dash-cat-table__sort" onClick={() => cycleSort('category')}>
                  Category{sortMark('category')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('flow')}>
                  Flow{sortMark('flow')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('income')}>
                  Income{sortMark('income')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('expense')}>
                  Expense{sortMark('expense')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('net')}>
                  Net{sortMark('net')}
                </th>
                <th
                  scope="col"
                  className="dash-cat-table__sort dash-num"
                  onClick={() => cycleSort('avg_monthly_net')}
                  title="Category net divided by the selected window length in months"
                >
                  Avg/mo net{sortMark('avg_monthly_net')}
                </th>
                <th
                  scope="col"
                  className="dash-cat-table__sort dash-num"
                  onClick={() => cycleSort('yearly_net_proj')}
                  title="Avg monthly net × 12 (simple projection)"
                >
                  Yr proj.{sortMark('yearly_net_proj')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('txn_count')}>
                  Txns{sortMark('txn_count')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('pct_in')}>
                  % inc{sortMark('pct_in')}
                </th>
                <th scope="col" className="dash-cat-table__sort dash-num" onClick={() => cycleSort('pct_ex')}>
                  % exp{sortMark('pct_ex')}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr
                  key={row.category}
                  className="dash-cat-table__row"
                  role="button"
                  tabIndex={0}
                  title="Open net drill-down for this category"
                  onClick={() => drillCategory(row.category, 'net')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      drillCategory(row.category, 'net');
                    }
                  }}
                >
                  <td className="dash-cat-table__cat">{row.category}</td>
                  <td className="dash-num">{formatMoney(flowOf(row))}</td>
                  <td className="dash-num dash-num--in">{formatMoney(row.income)}</td>
                  <td className="dash-num dash-num--out">{formatMoney(row.expense)}</td>
                  <td className={`dash-num ${row.net >= 0 ? 'dash-num--in' : 'dash-num--out'}`}>
                    {formatMoney(row.net)}
                  </td>
                  <td
                    className={`dash-num ${row.avg_monthly_net >= 0 ? 'dash-num--in' : 'dash-num--out'}`}
                  >
                    {formatMoney(row.avg_monthly_net)}
                  </td>
                  <td
                    className={`dash-num ${yearlyNetProjectionOf(row) >= 0 ? 'dash-num--in' : 'dash-num--out'}`}
                  >
                    {formatMoney(yearlyNetProjectionOf(row))}
                  </td>
                  <td className="dash-num">{row.txn_count.toLocaleString()}</td>
                  <td className="dash-num">{formatPct(row.pct_of_period_income)}</td>
                  <td className="dash-num">{formatPct(row.pct_of_period_expense)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="dash-cat-chart">
          <ResponsiveContainer width="100%" height={Math.min(560, 160 + chartRows.length * 24)}>
            <BarChart
              data={chartRows}
              layout="vertical"
              margin={{ top: 6, right: 12, left: 4, bottom: 0 }}
              barCategoryGap={4}
              barGap={2}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" horizontal={false} />
              <XAxis type="number" stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
              <YAxis
                dataKey="category"
                type="category"
                stroke="#aeb4c0"
                fontSize={10}
                width={108}
                tickFormatter={(v) => (String(v).length > 16 ? `${String(v).slice(0, 14)}…` : String(v))}
              />
              <Tooltip content={<CategoryChartTooltip />} />
              <Legend wrapperStyle={{ fontSize: '0.76rem' }} />
              <Bar dataKey="income" name="Income" fill="#37b24d" cursor="pointer">
                {chartRows.map((r, i) => (
                  <Cell
                    key={`inc-${i}`}
                    onClick={() => drillCategory(r.category, 'income')}
                  />
                ))}
              </Bar>
              <Bar dataKey="expense" name="Expense" fill="#fa5252" cursor="pointer">
                {chartRows.map((r, i) => (
                  <Cell
                    key={`exp-${i}`}
                    onClick={() => drillCategory(r.category, 'expense')}
                  />
                ))}
              </Bar>
              <Bar dataKey="net" name="Net" fill="#4c6ef5" cursor="pointer">
                {chartRows.map((r, i) => (
                  <Cell key={`net-${i}`} onClick={() => drillCategory(r.category, 'net')} />
                ))}
              </Bar>
              <Bar dataKey="avg_monthly_net" name="Avg/mo net" fill="#9775fa" cursor="pointer">
                {chartRows.map((r, i) => (
                  <Cell key={`avg-${i}`} onClick={() => drillCategory(r.category, 'net')} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="dash-cat-chart__hint">
            Click a bar group to drill by income, expense, or net for that category. Avg/mo net uses the same
            window month count as the header.
          </p>
        </div>
      )}
    </div>
  );
}

const OTHER_BUCKET_RE = /^\(other /i;

function SourceCategoryMatrixCard() {
  const navigate = useNavigate();
  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const metaReady = meta.isSuccess;
  const matrixUrl =
    '/api/dashboard/source-category-matrix?months=12&direction=expense&top_sources=10&top_categories=12';
  const { data, error, isPending } = useDashboardQuery<SourceCategoryMatrixResponse>(
    rev,
    matrixUrl,
    metaReady,
  );
  const maxCell = useMemo(() => {
    let m = 0;
    for (const row of data?.cells ?? []) {
      for (const v of row) m = Math.max(m, v);
    }
    return m > 0 ? m : 1;
  }, [data?.cells]);

  const cellBg = (v: number): CSSProperties => {
    const t = Math.min(1, v / maxCell);
    const a = 0.12 + t * 0.78;
    return { background: `rgba(250, 82, 82, ${a})` };
  };

  const onCellClick = (src: string, cat: string, amount: number, months: number) => {
    if (amount <= 0) return;
    if (OTHER_BUCKET_RE.test(src) || OTHER_BUCKET_RE.test(cat)) return;
    navigate(heatmapDetailSourceCategory('expense', src, cat, months));
  };

  return (
    <div className="dash-card">
      <h3 className="dash-card__title">Source × category (expenses)</h3>
      <p className="dash-card__sub">
        Last 12 months · expense amounts by bank/source and category. Click a cell for transactions.
      </p>
      {isPending ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error.message}</div>
      ) : !data?.sources?.length || !data?.categories?.length ? (
        <div className="dash-empty">No expense transactions in range.</div>
      ) : (
        <div className="dash-matrix-scroll">
          <table className="dash-matrix-table">
            <thead>
              <tr>
                <th className="dash-matrix-corner">Source \ Category</th>
                {data.categories.map((c) => (
                  <th key={c} className="dash-matrix-col-head" title={c}>
                    {c.length > 14 ? `${c.slice(0, 12)}…` : c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.sources.map((src, si) => (
                <tr key={`${src}-${si}`}>
                  <th className="dash-matrix-row-head" title={src}>
                    {src.length > 18 ? `${src.slice(0, 16)}…` : src}
                  </th>
                  {(data.cells[si] ?? []).map((v, ci) => (
                    <td
                      key={ci}
                      className="dash-matrix-cell dash-matrix-cell--click"
                      style={cellBg(v)}
                      title={`${formatMoney(v)} · ${(v / (data.row_totals[si] || 1)).toLocaleString(undefined, { style: 'percent', maximumFractionDigits: 1 })} of row`}
                      role="button"
                      tabIndex={0}
                      onClick={() => onCellClick(src, data.categories[ci] ?? '', v, data.months)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          onCellClick(src, data.categories[ci] ?? '', v, data.months);
                        }
                      }}
                    >
                      {v >= 1000 ? formatMoney(v, '') : v > 0 ? Math.round(v).toLocaleString() : '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function KpiStrip() {
  const meta = useLedgerMetaQuery();
  const rev = meta.isSuccess ? dashboardRevisionFromMeta(meta.data) : null;
  const summary = useDashboardQuery<DashboardSummary>(rev, '/api/dashboard/summary', meta.isSuccess);

  if (meta.isPending) return <div className="dash-empty">Loading KPIs…</div>;
  if (meta.isError) return null;
  if (summary.isPending) return <div className="dash-empty">Loading KPIs…</div>;
  if (summary.error) return <div className="dash-empty">Error: {summary.error.message}</div>;
  const data = summary.data;
  if (!data?.ledger_exists) {
    return (
      <div className="dash-empty">
        Ledger database not found. Run the pipeline (or compile a dataset) to populate <code>data/ledger.sqlite</code>.
      </div>
    );
  }
  const k = data.kpis;
  return (
    <div className="dash-kpi-grid">
      <KPI
        label="Net worth"
        value={formatMoney(k.net_worth_latest)}
        sub={k.net_worth_latest_date ? `as of ${k.net_worth_latest_date}` : ''}
        delta={k.net_worth_delta_mom}
      />
      <KPI label="Last 30d income" value={formatMoney(k.income_30d)} />
      <KPI label="Last 30d expense" value={formatMoney(k.expense_30d)} />
      <KPI
        label="Last 30d net"
        value={formatMoney(k.net_30d)}
        sub={k.savings_rate_30d != null ? `Savings rate ${formatPct(k.savings_rate_30d)}` : ''}
      />
      <KPI
        label="Transactions"
        value={k.transaction_count != null ? k.transaction_count.toLocaleString() : '—'}
        sub={k.last_ingest_date ? `Last ingest ${k.last_ingest_date}` : ''}
      />
      <KPI
        label="Need category"
        value={k.uncategorized_count != null ? k.uncategorized_count.toLocaleString() : '—'}
        cta={
          k.uncategorized_count && k.uncategorized_count > 0 ? { href: '/categorize/', label: 'Open queue' } : undefined
        }
      />
    </div>
  );
}

export default function Dashboard() {
  const meta = useLedgerMetaQuery();
  return (
    <div className="app-page dash-page">
      <header className="dash-header">
        <h1>Dashboard</h1>
        <p className="sub">Holdings, cash flow, and category activity in one place.</p>
      </header>
      {meta.isError ? (
        <div className="dash-empty" role="alert">
          Could not read ledger revision: {meta.error instanceof Error ? meta.error.message : String(meta.error)}
        </div>
      ) : null}
      <KpiStrip />

      <section className="dash-section" aria-labelledby="dash-holdings-heading">
        <h2 id="dash-holdings-heading" className="dash-section__title">
          Holdings
        </h2>
        <div className="dash-grid dash-grid--wealth">
          <AllocationDonutCard />
          <NetWorthStackedCard />
        </div>
      </section>

      <section className="dash-section" aria-labelledby="dash-cash-heading">
        <h2 id="dash-cash-heading" className="dash-section__title">
          Cash flow
        </h2>
        <CashflowCard />
      </section>

      <section className="dash-section" aria-labelledby="dash-cat-heading">
        <h2 id="dash-cat-heading" className="dash-section__title">
          Categories & sources
        </h2>
        <div className="dash-stack">
          <CategoryOverviewCard />
          <SourceCategoryMatrixCard />
        </div>
      </section>
    </div>
  );
}
