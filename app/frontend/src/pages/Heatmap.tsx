import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { useNavigate } from 'react-router-dom';

import PageShell from '../components/PageShell';
import { fetchJson } from '../lib/api';
import {
  heatmapDetailCategory,
  heatmapDetailCell,
  heatmapDetailMonth,
} from '../lib/drilldown';
import { he } from '../lib/he';
import { heatmapGridColors } from '../lib/heatmapColors';
import { formatCellMoney } from '../lib/heatmapFormat';
import {
  loadHeatmapPrefs,
  saveHeatmapPrefs,
  visibleMetrics,
  type Density,
  type HeatmapPrefs,
  type MetricKey,
  type MonthWindow,
  type ReportType,
  type Zoom,
} from '../lib/heatmapPrefs';
import {
  availableYears,
  axisStatsFromSlice,
  drillPeriodFromWindow,
  sliceHeatmapView,
  type HeatmapView,
  type SlicedHeatmapView,
} from '../lib/heatmapView';

import './Heatmap.css';

type HeatmapSnapshot = {
  ok: boolean;
  message?: string | null;
  source?: string;
  sourceStatus?: { ledger_path?: string; ledger_exists?: boolean; transaction_count?: number };
  views?: Partial<Record<ReportType, HeatmapView>>;
};

const VIEW_TABS: Array<{ key: ReportType; label: string }> = [
  { key: 'expense', label: he.heatmap.expense },
  { key: 'income', label: he.heatmap.income },
  { key: 'net', label: he.heatmap.net },
];

const METRIC_LABELS: Record<MetricKey, string> = {
  rowSum: he.heatmap.metricRowSum,
  rowAvg: he.heatmap.metricRowAvg,
  ytdSum: he.heatmap.metricYtdSum,
  ytdAvg: he.heatmap.metricYtdAvg,
  l12Sum: he.heatmap.metricL12Sum,
  l12Avg: he.heatmap.metricL12Avg,
};

function cellClickable(v: number, reportType: ReportType): boolean {
  if (!Number.isFinite(v)) return false;
  if (reportType === 'net') return v !== 0;
  return v > 0;
}

function formatPct(n: number): string {
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

type TipState =
  | { kind: 'cell'; r: number; c: number; x: number; y: number }
  | { kind: 'row'; r: number; x: number; y: number }
  | { kind: 'col'; c: number; x: number; y: number }
  | null;

function HeatmapTip({
  tip,
  view,
}: {
  tip: TipState;
  view: SlicedHeatmapView;
}) {
  if (!tip) return null;
  const style: CSSProperties = {
    position: 'fixed',
    left: Math.min(tip.x + 12, window.innerWidth - 280),
    top: Math.min(tip.y + 12, window.innerHeight - 220),
    zIndex: 1000,
    pointerEvents: 'none',
  };

  let body: ReactNode = null;
  if (tip.kind === 'cell') {
    const amount = view.values[tip.r]?.[tip.c];
    const n = amount == null || !Number.isFinite(amount) ? NaN : Number(amount);
    const row = axisStatsFromSlice(view, 'row', tip.r);
    const col = axisStatsFromSlice(view, 'col', tip.c);
    const shareMonth = row.total !== 0 && Number.isFinite(n) ? n / row.total : NaN;
    const shareCat = col.total !== 0 && Number.isFinite(n) ? n / col.total : NaN;
    body = (
      <>
        <div className="hm-tip-title">
          {view.months[tip.r]} · {view.categories[tip.c]}
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipAmount}</span>
          <span className="num">{Number.isFinite(n) ? formatCellMoney(n) : '—'}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipShareMonth}</span>
          <span className="num">{formatPct(shareMonth)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipShareCat}</span>
          <span className="num">{formatPct(shareCat)}</span>
        </div>
        <div className="hm-tip-sec">{he.heatmap.tipCategory}</div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipTotal}</span>
          <span className="num">{formatCellMoney(col.total)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipAvg}</span>
          <span className="num">{formatCellMoney(col.avg)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipMedian}</span>
          <span className="num">{formatCellMoney(col.median)}</span>
        </div>
        <div className="hm-tip-sec">{he.heatmap.tipMonth}</div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipTotal}</span>
          <span className="num">{formatCellMoney(row.total)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipAvg}</span>
          <span className="num">{formatCellMoney(row.avg)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipMedian}</span>
          <span className="num">{formatCellMoney(row.median)}</span>
        </div>
      </>
    );
  } else if (tip.kind === 'row') {
    const st = axisStatsFromSlice(view, 'row', tip.r);
    body = (
      <>
        <div className="hm-tip-title">{view.months[tip.r]}</div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipTotal}</span>
          <span className="num">{formatCellMoney(st.total)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipAvg}</span>
          <span className="num">{formatCellMoney(st.avg)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipMedian}</span>
          <span className="num">{formatCellMoney(st.median)}</span>
        </div>
      </>
    );
  } else {
    const st = axisStatsFromSlice(view, 'col', tip.c);
    body = (
      <>
        <div className="hm-tip-title">{view.categories[tip.c]}</div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipTotal}</span>
          <span className="num">{formatCellMoney(st.total)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipAvg}</span>
          <span className="num">{formatCellMoney(st.avg)}</span>
        </div>
        <div className="hm-tip-row">
          <span>{he.heatmap.tipMedian}</span>
          <span className="num">{formatCellMoney(st.median)}</span>
        </div>
      </>
    );
  }

  return (
    <div className="hm-tip" style={style} role="tooltip">
      {body}
    </div>
  );
}

function metricValue(view: SlicedHeatmapView, key: MetricKey, row: number): number {
  switch (key) {
    case 'rowSum':
      return view.rowTotals[row] ?? 0;
    case 'rowAvg':
      return view.rowAverages[row] ?? 0;
    case 'ytdSum':
      return view.rowYtdSums[row] ?? 0;
    case 'ytdAvg':
      return view.rowYtdAverages[row] ?? 0;
    case 'l12Sum':
      return view.rowRolling12Sums[row] ?? 0;
    case 'l12Avg':
      return view.rowRolling12Averages[row] ?? 0;
  }
}

function stickyRightStyle(indexFromMonth: number): CSSProperties {
  // indexFromMonth 0 = month col; 1+ = metrics
  if (indexFromMonth === 0) return { right: 0 };
  return {
    right: `calc(var(--hm-month-col-w) + (var(--hm-metric-col-w) * ${indexFromMonth - 1}))`,
  };
}

type GridProps = {
  view: SlicedHeatmapView;
  metrics: MetricKey[];
  monthWindow: MonthWindow;
  hasHiddenCats: boolean;
  onHideCategory: (name: string) => void;
};

function HeatmapGrid({
  view,
  metrics,
  monthWindow,
  hasHiddenCats,
  onHideCategory,
}: GridProps) {
  const navigate = useNavigate();
  const months = view.months;
  const cats = view.categories;
  const wrapRef = useRef<HTMLTableElement>(null);
  const [focusCell, setFocusCell] = useState<{ r: number; c: number } | null>(null);
  const [tip, setTip] = useState<TipState>(null);
  const tipTimer = useRef<number | null>(null);

  const { cellBg, cellFg } = useMemo(
    () => heatmapGridColors(view.zPaint, view.colorScale.scheme, view.colorScale.center),
    [view.zPaint, view.colorScale.scheme, view.colorScale.center],
  );

  const clearTipTimer = () => {
    if (tipTimer.current != null) {
      window.clearTimeout(tipTimer.current);
      tipTimer.current = null;
    }
  };

  const showTipDelayed = (next: TipState) => {
    clearTipTimer();
    tipTimer.current = window.setTimeout(() => setTip(next), 200);
  };

  const hideTip = () => {
    clearTipTimer();
    setTip(null);
  };

  useEffect(() => () => clearTipTimer(), []);

  const drill = drillPeriodFromWindow(monthWindow, months);

  const openCell = (ym: string, cat: string) => {
    navigate(heatmapDetailCell(view.reportType, ym, cat));
  };
  const openMonth = (ym: string) => {
    navigate(heatmapDetailMonth(view.reportType, ym));
  };
  const openCat = (cat: string) => {
    navigate(heatmapDetailCategory(view.reportType, cat, drill.period, drill.ymRange));
  };

  useEffect(() => {
    if (!focusCell) return;
    const el = wrapRef.current?.querySelector(
      `td.cell[data-r="${focusCell.r}"][data-c="${focusCell.c}"]`,
    ) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const rect = el?.getBoundingClientRect();
    if (rect) {
      setTip({
        kind: 'cell',
        r: focusCell.r,
        c: focusCell.c,
        x: rect.left,
        y: rect.bottom,
      });
    }
  }, [focusCell]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

      if (e.key === 'Escape') {
        setFocusCell(null);
        hideTip();
        return;
      }

      const rows = months.length;
      const cols = cats.length;
      if (!rows || !cols) return;

      if (e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        setFocusCell((prev) => {
          let r = prev?.r ?? 0;
          let c = prev?.c ?? 0;
          if (e.key === 'ArrowUp') r = Math.max(0, r - 1);
          if (e.key === 'ArrowDown') r = Math.min(rows - 1, r + 1);
          // RTL: ArrowLeft moves visually left → higher category index
          if (e.key === 'ArrowLeft') c = Math.min(cols - 1, c + 1);
          if (e.key === 'ArrowRight') c = Math.max(0, c - 1);
          return { r, c };
        });
        return;
      }

      if ((e.key === 'Enter' || e.key === ' ') && focusCell) {
        e.preventDefault();
        const raw = view.values[focusCell.r]?.[focusCell.c];
        const v = raw == null || !Number.isFinite(raw) ? NaN : Number(raw);
        if (Number.isFinite(v) && cellClickable(v, view.reportType)) {
          openCell(months[focusCell.r], cats[focusCell.c]);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- openCell/cats stable enough via view
  }, [focusCell, months, cats, view]);

  const contextMetric = (m: MetricKey) => m === 'ytdSum' || m === 'ytdAvg' || m === 'l12Sum' || m === 'l12Avg';

  return (
    <>
      <table className="hm-grid" ref={wrapRef} tabIndex={0}>
        <thead>
          <tr>
            <th rowSpan={2} className="corner" style={stickyRightStyle(0)}>
              {he.heatmap.corner}
            </th>
            {metrics.map((m, mi) => (
              <th
                key={m}
                rowSpan={2}
                className={`hm-metric-h${contextMetric(m) && hasHiddenCats ? ' hm-metric-context' : ''}`}
                style={stickyRightStyle(mi + 1)}
                title={contextMetric(m) && hasHiddenCats ? he.heatmap.contextHint : undefined}
              >
                {METRIC_LABELS[m].split(' ').map((part, i, arr) => (
                  <span key={i}>
                    {part}
                    {i < arr.length - 1 ? <br /> : null}
                  </span>
                ))}
              </th>
            ))}
            {cats.map((_, c) => {
              const t = formatCellMoney(view.columnTotals?.[c] ?? 0);
              const a = formatCellMoney(view.columnAverages?.[c] ?? 0);
              return (
                <th
                  key={c}
                  className="hm-colsum"
                  onMouseEnter={(e) =>
                    showTipDelayed({ kind: 'col', c, x: e.clientX, y: e.clientY })
                  }
                  onMouseLeave={hideTip}
                  onMouseMove={(e) => {
                    if (tip?.kind === 'col' && tip.c === c) {
                      setTip({ kind: 'col', c, x: e.clientX, y: e.clientY });
                    }
                  }}
                >
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
              <th
                key={i}
                className="hm-cat-h"
                onClick={() => openCat(c)}
                onMouseEnter={(e) =>
                  showTipDelayed({ kind: 'col', c: i, x: e.clientX, y: e.clientY })
                }
                onMouseLeave={hideTip}
              >
                <span className="hm-cat-name">{c}</span>
                <button
                  type="button"
                  className="hm-cat-hide"
                  title={he.heatmap.hideCategory}
                  aria-label={he.heatmap.hideCategory}
                  onClick={(e) => {
                    e.stopPropagation();
                    onHideCategory(c);
                  }}
                >
                  ×
                </button>
              </th>
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
                <th
                  className="row-h hm-month-h"
                  style={stickyRightStyle(0)}
                  onClick={() => openMonth(monthRaw)}
                  onMouseEnter={(e) =>
                    showTipDelayed({ kind: 'row', r: i, x: e.clientX, y: e.clientY })
                  }
                  onMouseLeave={hideTip}
                >
                  <span className="month-markers">
                    {isL12Boundary ? <span className="l12-chip">12m</span> : null}
                  </span>
                  <span className="month-label">{monthRaw}</span>
                </th>
                {metrics.map((m, mi) => (
                  <td
                    key={m}
                    className="hm-rowmetric"
                    style={stickyRightStyle(mi + 1)}
                  >
                    {formatCellMoney(metricValue(view, m, i))}
                  </td>
                ))}
                {cats.map((cat, j) => {
                  const raw = view.values?.[i]?.[j];
                  const v = raw == null || !Number.isFinite(raw) ? NaN : Number(raw);
                  const lab = Number.isFinite(v) ? formatCellMoney(v) : '';
                  const bg = cellBg?.[i]?.[j] ?? '#333337';
                  const fg = cellFg?.[i]?.[j] ?? '#f4f6fb';
                  const isClickable = Number.isFinite(v) && cellClickable(v, view.reportType);
                  const focused = focusCell?.r === i && focusCell?.c === j;
                  const cls =
                    (isClickable ? 'cell clickable' : 'cell') + (focused ? ' cell-focus' : '');
                  return (
                    <td
                      key={j}
                      className={cls}
                      data-r={i}
                      data-c={j}
                      style={{ backgroundColor: bg, color: fg, textShadow: 'none' }}
                      onClick={() => {
                        setFocusCell({ r: i, c: j });
                        if (isClickable) openCell(monthRaw, cat);
                      }}
                      onMouseEnter={(e) =>
                        showTipDelayed({
                          kind: 'cell',
                          r: i,
                          c: j,
                          x: e.clientX,
                          y: e.clientY,
                        })
                      }
                      onMouseLeave={hideTip}
                      onMouseMove={(e) => {
                        if (tip?.kind === 'cell' && tip.r === i && tip.c === j) {
                          setTip({
                            kind: 'cell',
                            r: i,
                            c: j,
                            x: e.clientX,
                            y: e.clientY,
                          });
                        }
                      }}
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
      <HeatmapTip tip={tip} view={view} />
    </>
  );
}

function Popover({
  label,
  open,
  onToggle,
  children,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className={`hm-pop${open ? ' open' : ''}`}>
      <button type="button" className="hm-tool-btn secondary" onClick={onToggle}>
        {label}
      </button>
      {open ? <div className="hm-pop-panel">{children}</div> : null}
    </div>
  );
}

export default function Heatmap() {
  const [snapshot, setSnapshot] = useState<HeatmapSnapshot | null>(null);
  const [prefs, setPrefs] = useState<HeatmapPrefs>(() => loadHeatmapPrefs());
  const [err, setErr] = useState('');
  const [refreshStatus, setRefreshStatus] = useState<string>(he.heatmap.loading);
  const [refreshing, setRefreshing] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [isFs, setIsFs] = useState(false);
  const [openPop, setOpenPop] = useState<'metrics' | 'cats' | 'window' | 'density' | null>(null);
  const [catQuery, setCatQuery] = useState('');
  const wrapRef = useRef<HTMLDivElement>(null);
  const catSearchRef = useRef<HTMLInputElement>(null);

  const updatePrefs = useCallback((patch: Partial<HeatmapPrefs> | ((p: HeatmapPrefs) => HeatmapPrefs)) => {
    setPrefs((prev) => {
      const next = typeof patch === 'function' ? patch(prev) : { ...prev, ...patch };
      saveHeatmapPrefs(next);
      return next;
    });
  }, []);

  const loadSnapshot = useCallback(async () => {
    setRefreshStatus(he.heatmap.loading);
    try {
      const r = await fetchJson<HeatmapSnapshot>('/heatmap/api/data', { cache: 'no-store' });
      const data = r.data;
      setSnapshot(data);
      const ledgerExists = data.sourceStatus?.ledger_exists !== false;
      if (!data.ok) {
        setErr(data.message || 'טעינת נתונים נכשלה');
        setRefreshStatus(!ledgerExists ? he.heatmap.noLedger : '');
        return;
      }
      setErr('');
      const st = data.sourceStatus || {};
      const cnt =
        typeof st.transaction_count === 'number' && st.transaction_count >= 0
          ? ` · ${st.transaction_count} ${he.heatmap.rows}`
          : '';
      setRefreshStatus(
        `${he.heatmap.ledger}: ${(st.ledger_path || data.source || '').replace(/\\/g, '/')}${cnt}`,
      );
    } catch (e) {
      setErr('שגיאת רשת: ' + (e instanceof Error ? e.message : String(e)));
      setRefreshStatus('');
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    const root = document.getElementById('app-shell');
    if (!root) return;
    root.classList.toggle('app-shell--heatmap-focus', focusMode);
    return () => root.classList.remove('app-shell--heatmap-focus');
  }, [focusMode]);

  useEffect(() => {
    const onFs = () => setIsFs(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
        e.preventDefault();
        setOpenPop('cats');
        window.setTimeout(() => catSearchRef.current?.focus(), 0);
        return;
      }
      if (e.key === 'Escape') {
        if (openPop) {
          setOpenPop(null);
          return;
        }
        if (document.fullscreenElement) {
          void document.exitFullscreen();
          return;
        }
        if (focusMode) setFocusMode(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [focusMode, openPop]);

  const onRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshStatus(he.heatmap.reloading);
    try {
      const r = await fetchJson<{ ok?: boolean; message?: string }>('/heatmap/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const j = r.data;
      if (!j.ok) {
        setErr(j.message || he.common.error);
        setRefreshStatus(j.message || '');
      } else {
        setErr('');
        setRefreshStatus(j.message || he.heatmap.updated);
      }
    } catch (e) {
      setErr('שגיאת רשת: ' + (e instanceof Error ? e.message : String(e)));
      setRefreshStatus('');
    } finally {
      setRefreshing(false);
      void loadSnapshot();
    }
  };

  const toggleFullscreen = async () => {
    const el = wrapRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await el.requestFullscreen();
    } catch {
      /* ignore */
    }
  };

  const ledgerExists = snapshot?.sourceStatus?.ledger_exists !== false;
  const rawView = snapshot?.views?.[prefs.reportType] ?? null;

  const sliced = useMemo(() => {
    if (!rawView) return null;
    return sliceHeatmapView(rawView, {
      monthWindow: prefs.monthWindow,
      hiddenCategories: prefs.hiddenCategories,
      hideEmpty: prefs.hideEmpty,
    });
  }, [rawView, prefs.monthWindow, prefs.hiddenCategories, prefs.hideEmpty]);

  const metrics = useMemo(() => visibleMetrics(prefs.hiddenMetrics), [prefs.hiddenMetrics]);
  const years = useMemo(() => (rawView ? availableYears(rawView.months) : []), [rawView]);

  const densityClass =
    `heatmap-page density-${prefs.density} zoom-${prefs.zoom}` +
    (focusMode ? ' heatmap-page--focus' : '');

  const filteredCatNames = useMemo(() => {
    if (!rawView) return [];
    const q = catQuery.trim().toLowerCase();
    return rawView.categories.filter((c) => !q || c.toLowerCase().includes(q));
  }, [rawView, catQuery]);

  const toggleMetric = (m: MetricKey) => {
    updatePrefs((p) => {
      const set = new Set(p.hiddenMetrics);
      if (set.has(m)) set.delete(m);
      else set.add(m);
      return { ...p, hiddenMetrics: [...set] };
    });
  };

  const toggleCategory = (name: string) => {
    updatePrefs((p) => {
      const set = new Set(p.hiddenCategories);
      if (set.has(name)) set.delete(name);
      else set.add(name);
      return { ...p, hiddenCategories: [...set] };
    });
  };

  const setWindow = (mw: MonthWindow) => updatePrefs({ monthWindow: mw });

  return (
    <PageShell
      title={he.heatmap.title}
      sub={focusMode ? undefined : he.heatmap.sub}
      width="full"
      className={densityClass}
      actions={
        <div className="heatmap-toolbar">
          <button
            type="button"
            className="hm-tool-btn secondary"
            onClick={() => setFocusMode((v) => !v)}
            title={focusMode ? he.heatmap.focusExit : he.heatmap.focus}
          >
            {focusMode ? he.heatmap.focusExit : he.heatmap.focus}
          </button>
          <button
            type="button"
            className="hm-tool-btn secondary"
            onClick={() => void toggleFullscreen()}
            title={isFs ? he.heatmap.fullscreenExit : he.heatmap.fullscreen}
          >
            {isFs ? he.heatmap.fullscreenExit : he.heatmap.fullscreen}
          </button>

          <Popover
            label={he.heatmap.density}
            open={openPop === 'density'}
            onToggle={() => setOpenPop(openPop === 'density' ? null : 'density')}
          >
            <div className="hm-pop-section">{he.heatmap.density}</div>
            {(['comfortable', 'compact'] as Density[]).map((d) => (
              <label key={d} className="hm-check">
                <input
                  type="radio"
                  name="hm-density"
                  checked={prefs.density === d}
                  onChange={() => updatePrefs({ density: d })}
                />
                {d === 'compact' ? he.heatmap.densityCompact : he.heatmap.densityComfortable}
              </label>
            ))}
            <div className="hm-pop-section">{he.heatmap.zoom}</div>
            {(['90', '100', '115'] as Zoom[]).map((z) => (
              <label key={z} className="hm-check">
                <input
                  type="radio"
                  name="hm-zoom"
                  checked={prefs.zoom === z}
                  onChange={() => updatePrefs({ zoom: z })}
                />
                {z}%
              </label>
            ))}
          </Popover>

          <Popover
            label={he.heatmap.metrics}
            open={openPop === 'metrics'}
            onToggle={() => setOpenPop(openPop === 'metrics' ? null : 'metrics')}
          >
            {(Object.keys(METRIC_LABELS) as MetricKey[]).map((m) => (
              <label key={m} className="hm-check">
                <input
                  type="checkbox"
                  checked={!prefs.hiddenMetrics.includes(m)}
                  onChange={() => toggleMetric(m)}
                />
                {METRIC_LABELS[m]}
              </label>
            ))}
          </Popover>

          <Popover
            label={he.heatmap.categories}
            open={openPop === 'cats'}
            onToggle={() => setOpenPop(openPop === 'cats' ? null : 'cats')}
          >
            <input
              ref={catSearchRef}
              type="text"
              className="hm-cat-search"
              placeholder={he.heatmap.searchCategories}
              value={catQuery}
              onChange={(e) => setCatQuery(e.target.value)}
            />
            <label className="hm-check">
              <input
                type="checkbox"
                checked={prefs.hideEmpty}
                onChange={(e) => updatePrefs({ hideEmpty: e.target.checked })}
              />
              {he.heatmap.hideEmpty}
            </label>
            <button
              type="button"
              className="hm-tool-btn secondary hm-pop-full"
              onClick={() => updatePrefs({ hiddenCategories: [] })}
            >
              {he.heatmap.showAllCategories}
            </button>
            <div className="hm-cat-list">
              {filteredCatNames.map((name) => (
                <label key={name} className="hm-check">
                  <input
                    type="checkbox"
                    checked={!prefs.hiddenCategories.includes(name)}
                    onChange={() => toggleCategory(name)}
                  />
                  {name}
                </label>
              ))}
            </div>
          </Popover>

          <Popover
            label={he.heatmap.monthWindow}
            open={openPop === 'window'}
            onToggle={() => setOpenPop(openPop === 'window' ? null : 'window')}
          >
            {(
              [
                ['all', he.heatmap.windowAll],
                ['12m', he.heatmap.window12m],
                ['ytd', he.heatmap.windowYtd],
                ['year', he.heatmap.windowYear],
                ['custom', he.heatmap.windowCustom],
              ] as const
            ).map(([kind, label]) => (
              <label key={kind} className="hm-check">
                <input
                  type="radio"
                  name="hm-window"
                  checked={prefs.monthWindow.kind === kind}
                  onChange={() =>
                    setWindow({
                      ...prefs.monthWindow,
                      kind,
                      year: prefs.monthWindow.year || years[0],
                    })
                  }
                />
                {label}
              </label>
            ))}
            {prefs.monthWindow.kind === 'year' ? (
              <label className="hm-field">
                <span>{he.heatmap.year}</span>
                <select
                  value={prefs.monthWindow.year || years[0] || ''}
                  onChange={(e) =>
                    setWindow({ kind: 'year', year: e.target.value })
                  }
                >
                  {years.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {prefs.monthWindow.kind === 'custom' ? (
              <div className="hm-custom-range">
                <label className="hm-field">
                  <span>{he.heatmap.from}</span>
                  <input
                    type="month"
                    value={prefs.monthWindow.startYm || ''}
                    onChange={(e) =>
                      setWindow({
                        kind: 'custom',
                        startYm: e.target.value,
                        endYm: prefs.monthWindow.endYm,
                      })
                    }
                  />
                </label>
                <label className="hm-field">
                  <span>{he.heatmap.to}</span>
                  <input
                    type="month"
                    value={prefs.monthWindow.endYm || ''}
                    onChange={(e) =>
                      setWindow({
                        kind: 'custom',
                        startYm: prefs.monthWindow.startYm,
                        endYm: e.target.value,
                      })
                    }
                  />
                </label>
              </div>
            ) : null}
          </Popover>

          <button
            type="button"
            className="btn-refresh"
            disabled={refreshing || !ledgerExists}
            onClick={() => void onRefresh()}
            title={he.heatmap.reload}
          >
            {he.heatmap.reload}
          </button>
          {!focusMode ? <span className="refresh-status">{refreshStatus}</span> : null}
        </div>
      }
    >
      {!focusMode ? <p className="subtle">{he.heatmap.ledgerHint}</p> : null}
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
            className={prefs.reportType === t.key ? 'active' : ''}
            onClick={() => updatePrefs({ reportType: t.key })}
          >
            {t.label}
          </button>
        ))}
      </div>
      {!focusMode ? (
        <div className="heatmap-title">
          {sliced ? `${sliced.title} — ${he.heatmap.clickHint}` : ''}
        </div>
      ) : null}
      <div className="heatmap-wrap" ref={wrapRef}>
        {sliced && sliced.months.length && sliced.categories.length ? (
          <HeatmapGrid
            view={sliced}
            metrics={metrics}
            monthWindow={prefs.monthWindow}
            hasHiddenCats={prefs.hiddenCategories.length > 0 || prefs.hideEmpty}
            onHideCategory={(name) =>
              updatePrefs((p) => ({
                ...p,
                hiddenCategories: p.hiddenCategories.includes(name)
                  ? p.hiddenCategories
                  : [...p.hiddenCategories, name],
              }))
            }
          />
        ) : (
          <p className="no-data">{he.heatmap.empty}</p>
        )}
      </div>
    </PageShell>
  );
}
