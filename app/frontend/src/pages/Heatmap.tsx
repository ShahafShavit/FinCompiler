import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { HeatmapStatsTables, type StatsTabular } from '../components/HeatmapStatsTables';
import { fetchJson } from '../lib/api';
import { heatmapGridColors, type HeatmapScheme } from '../lib/heatmapColors';
import { formatCellMoney } from '../lib/heatmapFormat';

import './Heatmap.css';

type ReportType = 'expense' | 'income' | 'net';

/** Payload slice from ``GET /heatmap/api/data`` (numeric matrices; colors computed client-side). */
type HeatmapView = {
  title: string;
  reportType: ReportType;
  months: string[];
  categories: string[];
  values: (number | null)[][];
  zPaint: (number | null)[][];
  colorScale: { scheme: HeatmapScheme; center: number | null };
  columnTotals: number[];
  columnAverages: number[];
  rowTotals: number[];
  rowAverages: number[];
  rowYtdSums: number[];
  rowYtdAverages: number[];
  rowRolling12Sums: number[];
  rowRolling12Averages: number[];
};

type HeatmapSnapshot = {
  ok: boolean;
  message?: string | null;
  source?: string;
  sourceStatus?: { ledger_path?: string; ledger_exists?: boolean; transaction_count?: number };
  views?: Partial<Record<ReportType, HeatmapView>>;
  statsTables?: Partial<
    Record<ReportType, { byCategory: StatsTabular; byMonth: StatsTabular }>
  >;
};

const VIEW_TABS: Array<{ key: ReportType; label: string }> = [
  { key: 'expense', label: 'הוצאות' },
  { key: 'income', label: 'הכנסות' },
  { key: 'net', label: 'נטו' },
];

function cellClickable(v: number, reportType: ReportType): boolean {
  if (!Number.isFinite(v)) return false;
  if (reportType === 'net') return v !== 0;
  return v > 0;
}

function HeatmapGrid({ view }: { view: HeatmapView }) {
  const navigate = useNavigate();
  const months = view.months;
  const cats = view.categories;

  const { cellBg, cellFg } = useMemo(
    () =>
      heatmapGridColors(view.zPaint, view.colorScale.scheme, view.colorScale.center),
    [view.zPaint, view.colorScale.scheme, view.colorScale.center],
  );

  const onCellClick = (ym: string, cat: string) => {
    navigate({
      pathname: '/heatmap/detail',
      search:
        '?' +
        new URLSearchParams({
          type: view.reportType,
          ym,
          cat,
        }).toString(),
    });
  };

  return (
    <table className="hm-grid">
      <thead>
        <tr>
          <th rowSpan={2} className="corner">
            חודש \ קטגוריה
          </th>
          <th rowSpan={2} className="hm-metric-h hm-rowsum-h">
            סה״כ
            <br />
            חודש
          </th>
          <th rowSpan={2} className="hm-metric-h hm-rowavg-h">
            ממוצע
            <br />
            חודש
          </th>
          <th rowSpan={2} className="hm-metric-h hm-ytdsum-h">
            YTD
            <br />
            סה״כ
          </th>
          <th rowSpan={2} className="hm-metric-h hm-ytdavg-h">
            YTD
            <br />
            ממוצע
          </th>
          <th rowSpan={2} className="hm-metric-h hm-l12sum-h">
            12M
            <br />
            סה״כ
          </th>
          <th rowSpan={2} className="hm-metric-h hm-l12avg-h">
            12M
            <br />
            ממוצע
          </th>
          {cats.map((_, c) => {
            const t = formatCellMoney(view.columnTotals?.[c] ?? 0);
            const a = formatCellMoney(view.columnAverages?.[c] ?? 0);
            return (
              <th key={c} className="hm-colsum">
                <div className="colsum-wrap">
                  <div>
                    <span className="metric-label">Σ</span>
                    <span className="metric-val">{t}</span>
                  </div>
                  <div>
                    <span className="metric-label">Avg</span>
                    <span className="metric-val">{a}</span>
                  </div>
                </div>
              </th>
            );
          })}
        </tr>
        <tr>
          {cats.map((c, i) => (
            <th key={i}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {months.map((monthRaw, i) => {
          const prevMonthRaw = i > 0 ? String(months[i - 1] || '') : '';
          const year = monthRaw.slice(0, 4);
          const prevYear = prevMonthRaw.slice(0, 4);
          const isYearBoundary = i > 0 && year && prevYear && year !== prevYear;
          const isL12Boundary = i > 0 && i % 12 === 0;
          const rowClass =
            (isYearBoundary ? ' year-start' : '') + (isL12Boundary ? ' group-boundary' : '');
          return (
            <tr key={i} className={rowClass.trim()}>
              <th className="row-h">
                <span className="month-markers">
                  {isL12Boundary ? <span className="l12-chip">12m</span> : null}
                </span>
                <span className="month-label">{monthRaw}</span>
              </th>
              <td className="hm-rowtot hm-rowmetric">
                {formatCellMoney(view.rowTotals?.[i] ?? 0)}
              </td>
              <td className="hm-rowavg hm-rowmetric">
                {formatCellMoney(view.rowAverages?.[i] ?? 0)}
              </td>
              <td className="hm-ytdsum hm-rowmetric">
                {formatCellMoney(view.rowYtdSums?.[i] ?? 0)}
              </td>
              <td className="hm-ytdavg hm-rowmetric">
                {formatCellMoney(view.rowYtdAverages?.[i] ?? 0)}
              </td>
              <td className="hm-l12sum hm-rowmetric">
                {formatCellMoney(view.rowRolling12Sums?.[i] ?? 0)}
              </td>
              <td className="hm-l12avg hm-rowmetric">
                {formatCellMoney(view.rowRolling12Averages?.[i] ?? 0)}
              </td>
              {cats.map((cat, j) => {
                const raw = view.values?.[i]?.[j];
                const v = raw == null || !Number.isFinite(raw) ? NaN : Number(raw);
                const lab = Number.isFinite(v) ? formatCellMoney(v) : '';
                const bg = cellBg?.[i]?.[j] ?? '#333337';
                const fg = cellFg?.[i]?.[j] ?? '#f4f6fb';
                const isClickable = Number.isFinite(v) && cellClickable(v, view.reportType);
                const cls = isClickable ? 'cell clickable' : 'cell';
                const ym = months[i];
                const handleClick = isClickable ? () => onCellClick(ym, cat) : undefined;
                return (
                  <td
                    key={j}
                    className={cls}
                    style={{ backgroundColor: bg, color: fg, textShadow: 'none' }}
                    onClick={handleClick}
                  >
                    {lab}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function Heatmap() {
  const [snapshot, setSnapshot] = useState<HeatmapSnapshot | null>(null);
  const [view, setView] = useState<ReportType>('expense');
  const [err, setErr] = useState<string>('');
  const [refreshStatus, setRefreshStatus] = useState<string>('Loading…');
  const [refreshing, setRefreshing] = useState(false);

  const loadSnapshot = useCallback(async () => {
    setRefreshStatus('Loading…');
    try {
      const r = await fetchJson<HeatmapSnapshot>('/heatmap/api/data', { cache: 'no-store' });
      const data = r.data;
      setSnapshot(data);
      const ledgerExists = data.sourceStatus?.ledger_exists !== false;
      if (!data.ok) {
        setErr(data.message || 'טעינת נתונים נכשלה');
        if (!ledgerExists) {
          setRefreshStatus('No ledger.sqlite — run compile first.');
        } else {
          setRefreshStatus('');
        }
        return;
      }
      setErr('');
      const st = data.sourceStatus || {};
      const cnt =
        typeof st.transaction_count === 'number' && st.transaction_count >= 0
          ? ` · ${st.transaction_count} rows`
          : '';
      setRefreshStatus(`Ledger: ${st.ledger_path || data.source || ''}${cnt}`);
    } catch (e) {
      setErr('שגיאת רשת: ' + (e instanceof Error ? e.message : String(e)));
      setRefreshStatus('');
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  const onRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshStatus('Reloading from ledger…');
    try {
      const r = await fetchJson<{ ok?: boolean; message?: string }>('/heatmap/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const j = r.data;
      if (!j.ok) {
        setErr(j.message || 'Refresh failed');
        setRefreshStatus(j.message || '');
      } else {
        setErr('');
        setRefreshStatus(j.message || 'Updated.');
      }
    } catch (e) {
      setErr('שגיאת רשת: ' + (e instanceof Error ? e.message : String(e)));
      setRefreshStatus('');
    } finally {
      setRefreshing(false);
      void loadSnapshot();
    }
  };

  const ledgerExists = snapshot?.sourceStatus?.ledger_exists !== false;
  const currentView = snapshot?.views?.[view] ?? null;
  const statsTab = snapshot?.statsTables?.[view];

  return (
    <div className="heatmap-page">
      <h1>מפת חום — הוצאות / הכנסות / נטו</h1>
      <p className="subtle">
        מקור אמת: מסד ה־SQLite (<code>ledger.sqlite</code>) — אותן תנועות כמו בקטלוג ובדחיפה ל־Google
        Sheets. לחיצה על תא פותחת פירוט תנועות. <strong>רענון</strong> מנקה מטמון וטוען מחדש מהמסד.
      </p>
      <div className="heatmap-toolbar">
        <button
          type="button"
          className="btn-refresh"
          disabled={refreshing || !ledgerExists}
          onClick={() => void onRefresh()}
          title="Clear cache and reload from SQLite ledger"
        >
          Reload from ledger
        </button>
        <span className="refresh-status">{refreshStatus}</span>
      </div>
      {err ? (
        <div className="err-banner" style={{ display: 'block' }}>
          {err}
        </div>
      ) : null}
      <div className="tabs">
        {VIEW_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={view === t.key ? 'active' : ''}
            onClick={() => setView(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="heatmap-title">
        {currentView ? `${currentView.title} — לחץ על תא לפירוט` : ''}
      </div>
      <div className="heatmap-wrap">
        {currentView ? (
          <HeatmapGrid view={currentView} />
        ) : (
          <p className="no-data">אין נתונים</p>
        )}
      </div>
      <div className="stats-container">
        {statsTab ? (
          <HeatmapStatsTables
            reportType={view}
            byCategory={statsTab.byCategory}
            byMonth={statsTab.byMonth}
          />
        ) : (
          <>
            <div className="stats-table-container">
              <h2>סיכום לפי קטגוריה</h2>
              <p className="no-data">אין נתונים</p>
            </div>
            <div className="stats-table-container">
              <h2>סיכום לפי חודש</h2>
              <p className="no-data">אין נתונים</p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
