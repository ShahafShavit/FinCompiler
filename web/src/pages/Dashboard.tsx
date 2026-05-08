import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ComposedChart,
} from 'recharts';

import { formatMoney, formatPct, getJson } from '../lib/api';
import { heatmapDetailCategory, heatmapDetailMonth, heatmapDetailSource } from '../lib/drilldown';
import type {
  AllocationRow,
  AllocationTimelineRow,
  CashflowRow,
  DashboardSummary,
  NetWorthRow,
  RowsResponse,
  SourceRow,
  TopCategoryRow,
} from '../lib/dashboardTypes';

import './Dashboard.css';

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

function useFetch<T>(url: string): { data: T | null; error: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getJson<T>(url)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return { data, error, loading };
}

function NetWorthCard() {
  const { data, error, loading } = useFetch<RowsResponse<NetWorthRow>>('/api/dashboard/networth-timeline');
  return (
    <div className="dash-card">
      <h3 className="dash-card__title">Net worth over time</h3>
      <p className="dash-card__sub">Sum of holdings_balance per as-of date.</p>
      {/* Holdings snapshot data: chart click-through deferred (no transaction rows). */}
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : !data?.rows?.length ? (
        <div className="dash-empty">No holdings snapshots yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={data.rows} margin={{ top: 6, right: 14, left: 6, bottom: 0 }}>
            <defs>
              <linearGradient id="nw_grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#4c6ef5" stopOpacity={0.55} />
                <stop offset="95%" stopColor="#4c6ef5" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" />
            <XAxis dataKey="as_of_date" stroke="#aeb4c0" fontSize={11} />
            <YAxis stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
            <Tooltip content={<MoneyTooltip />} />
            <Area
              type="monotone"
              dataKey="total_ils"
              name="Net worth"
              stroke="#4c6ef5"
              strokeWidth={2}
              fill="url(#nw_grad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function AllocationDonutCard() {
  const { data, error, loading } = useFetch<{
    ok: boolean;
    rows: AllocationRow[];
    as_of_date: string | null;
  }>('/api/dashboard/allocation');
  const filtered = useMemo(
    () => (data?.rows ?? []).filter((r) => Math.abs(r.balance_ils) > 0.005),
    [data?.rows],
  );
  return (
    <div className="dash-card">
      <h3 className="dash-card__title">
        Allocation now {data?.as_of_date ? <span className="dash-card__sub" style={{ marginInlineStart: '0.5rem' }}>({data.as_of_date})</span> : null}
      </h3>
      {/* Holdings snapshot data: chart click-through deferred. */}
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : filtered.length === 0 ? (
        <div className="dash-empty">No holdings snapshot yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <PieChart>
            <Pie
              data={filtered}
              dataKey="balance_ils"
              nameKey="activity_type"
              innerRadius={60}
              outerRadius={95}
              paddingAngle={1}
            >
              {filtered.map((row) => (
                <Cell key={row.activity_type} fill={hashColor(row.activity_type)} />
              ))}
            </Pie>
            <Tooltip content={<MoneyTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

type AllocPivot = { as_of_date: string } & Record<string, number | string>;

function AllocationTimelineCard() {
  const { data, error, loading } = useFetch<{
    ok: boolean;
    rows: AllocationTimelineRow[];
    activity_types: string[];
  }>('/api/dashboard/allocation-timeline');
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
    <div className="dash-card">
      <h3 className="dash-card__title">Allocation over time</h3>
      <p className="dash-card__sub">Stacked balances per activity type.</p>
      {/* Holdings snapshot data: chart click-through deferred. */}
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : rows.length === 0 ? (
        <div className="dash-empty">No holdings snapshots yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={rows} margin={{ top: 6, right: 14, left: 6, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" />
            <XAxis dataKey="as_of_date" stroke="#aeb4c0" fontSize={11} />
            <YAxis stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
            <Tooltip content={<MoneyTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
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
  const { data, error, loading } = useFetch<RowsResponse<CashflowRow>>('/api/dashboard/cashflow-monthly?months=24');
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
        Last 24 months · income (positive), expense (negative), net line. Click a bar or net point for transactions.
      </p>
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : rows.length === 0 ? (
        <div className="dash-empty">No transactions yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={rows} margin={{ top: 6, right: 14, left: 6, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" />
            <XAxis dataKey="month" stroke="#aeb4c0" fontSize={11} />
            <YAxis stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
            <Tooltip content={<MoneyTooltip />} />
            <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
            <Bar
              dataKey="income"
              name="Income"
              fill="#37b24d"
              stackId="cf"
              cursor="pointer"
              onClick={(d: unknown) => {
                const m = (d as CashflowRow)?.month;
                if (m) navigate(heatmapDetailMonth('income', m));
              }}
            />
            <Bar
              dataKey="expenseNeg"
              name="Expense"
              fill="#fa5252"
              stackId="cf"
              cursor="pointer"
              onClick={(d: unknown) => {
                const m = (d as CashflowRow)?.month;
                if (m) navigate(heatmapDetailMonth('expense', m));
              }}
            />
            <Line
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

function TopCategoriesCard() {
  const navigate = useNavigate();
  const { data, error, loading } = useFetch<RowsResponse<TopCategoryRow>>(
    '/api/dashboard/top-categories?period=12m&type=expense&limit=10',
  );
  return (
    <div className="dash-card dash-card--chart-click">
      <h3 className="dash-card__title">Top expense categories</h3>
      <p className="dash-card__sub">Last 12 months · top 10 by total spend. Click a bar for transactions.</p>
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : !data?.rows?.length ? (
        <div className="dash-empty">No categorized expenses yet.</div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(220, data.rows.length * 28)}>
          <BarChart data={data.rows} layout="vertical" margin={{ top: 6, right: 14, left: 6, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2b2c33" horizontal={false} />
            <XAxis type="number" stroke="#aeb4c0" fontSize={11} tickFormatter={(v) => formatMoney(v as number, '')} />
            <YAxis dataKey="category" type="category" stroke="#aeb4c0" fontSize={11} width={120} />
            <Tooltip content={<MoneyTooltip />} />
            <Bar
              dataKey="amount"
              name="Spend"
              fill="#fa5252"
              cursor="pointer"
              onClick={(d: unknown) => {
                const cat = (d as TopCategoryRow)?.category;
                if (cat) navigate(heatmapDetailCategory('expense', cat, '12m'));
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function SourcesCard() {
  const navigate = useNavigate();
  const { data, error, loading } = useFetch<RowsResponse<SourceRow>>('/api/dashboard/sources?months=12');
  return (
    <div className="dash-card">
      <h3 className="dash-card__title">Source mix</h3>
      <p className="dash-card__sub">Last 12 months · grouped by מקור עסקה. Click a row for all transactions from that source.</p>
      {loading ? (
        <div className="dash-empty">Loading…</div>
      ) : error ? (
        <div className="dash-empty">Error: {error}</div>
      ) : !data?.rows?.length ? (
        <div className="dash-empty">No transactions yet.</div>
      ) : (
        <table className="dash-source-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>#</th>
              <th>Expense</th>
              <th>Income</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.slice(0, 12).map((r) => (
              <tr
                key={r.source}
                className="dash-source-row-click"
                role="button"
                tabIndex={0}
                onClick={() => navigate(heatmapDetailSource(r.source, 12))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    navigate(heatmapDetailSource(r.source, 12));
                  }
                }}
              >
                <td>{r.source}</td>
                <td>{r.count}</td>
                <td>{formatMoney(r.expense)}</td>
                <td>{formatMoney(r.income)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function KpiStrip() {
  const { data, error, loading } = useFetch<DashboardSummary>('/api/dashboard/summary');
  if (loading) return <div className="dash-empty">Loading KPIs…</div>;
  if (error) return <div className="dash-empty">Error: {error}</div>;
  if (!data?.ledger_exists) {
    return (
      <div className="dash-empty">
        Ledger database not found. Run the pipeline (or compile a dataset) to populate{' '}
        <code>data/ledger.sqlite</code>.
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
          k.uncategorized_count && k.uncategorized_count > 0
            ? { href: '/categorize/', label: 'Open queue' }
            : undefined
        }
      />
    </div>
  );
}

export default function Dashboard() {
  return (
    <div className="app-page">
      <h1>Dashboard</h1>
      <p className="sub">Overview of holdings and transactions across your ledger.</p>
      <KpiStrip />
      <div className="dash-grid">
        <NetWorthCard />
        <AllocationDonutCard />
      </div>
      <div className="dash-grid">
        <AllocationTimelineCard />
        <CashflowCard />
      </div>
      <div className="dash-grid">
        <TopCategoriesCard />
        <SourcesCard />
      </div>
    </div>
  );
}
