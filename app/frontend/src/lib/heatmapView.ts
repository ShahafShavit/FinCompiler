import type { HeatmapScheme } from './heatmapColors';
import type { MonthWindow, ReportType } from './heatmapPrefs';

/** Payload slice from ``GET /heatmap/api/data``. */
export type HeatmapView = {
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

export type AxisStats = {
  total: number;
  avg: number;
  median: number;
};

export type SlicedHeatmapView = HeatmapView & {
  columnMedians: number[];
  rowMedians: number[];
  /** Original (pre-slice) category index for each visible column — unused after slice. */
};

function finite(v: number | null | undefined): number | null {
  if (v == null || !Number.isFinite(v)) return null;
  return Number(v);
}

function sumFinite(vals: (number | null)[]): number {
  let s = 0;
  for (const v of vals) {
    const n = finite(v);
    if (n != null) s += n;
  }
  return s;
}

function meanFinite(vals: (number | null)[]): number {
  const xs: number[] = [];
  for (const v of vals) {
    const n = finite(v);
    if (n != null) xs.push(n);
  }
  if (!xs.length) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function medianFinite(vals: (number | null)[]): number {
  const xs: number[] = [];
  for (const v of vals) {
    const n = finite(v);
    if (n != null) xs.push(n);
  }
  if (!xs.length) return 0;
  xs.sort((a, b) => a - b);
  const mid = Math.floor(xs.length / 2);
  if (xs.length % 2 === 0) return (xs[mid - 1] + xs[mid]) / 2;
  return xs[mid];
}

function ymOk(ym: string): boolean {
  return /^\d{4}-\d{2}$/.test(ym);
}

/** Months are newest-first from the API. */
export function filterMonthIndices(months: string[], window: MonthWindow): number[] {
  const all = months.map((_, i) => i);
  if (!months.length) return [];
  if (window.kind === 'all') return all;

  if (window.kind === '12m') {
    return all.slice(0, Math.min(12, months.length));
  }

  if (window.kind === 'ytd') {
    const newest = months[0];
    if (!ymOk(newest)) return all;
    const year = newest.slice(0, 4);
    return all.filter((i) => months[i].startsWith(year + '-'));
  }

  if (window.kind === 'year') {
    const y = (window.year || '').trim();
    if (!/^\d{4}$/.test(y)) return all;
    return all.filter((i) => months[i].startsWith(y + '-'));
  }

  // custom
  let start = window.startYm || '';
  let end = window.endYm || '';
  if (!ymOk(start) && !ymOk(end)) return all;
  if (!ymOk(start)) start = '0000-00';
  if (!ymOk(end)) end = '9999-99';
  if (start > end) {
    const t = start;
    start = end;
    end = t;
  }
  return all.filter((i) => {
    const m = months[i];
    return ymOk(m) && m >= start && m <= end;
  });
}

export function categoryIsEmpty(
  values: (number | null)[][],
  monthIdxs: number[],
  catIdx: number,
): boolean {
  for (const ri of monthIdxs) {
    const n = finite(values[ri]?.[catIdx]);
    if (n != null && n !== 0) return false;
  }
  return true;
}

export type SliceOptions = {
  monthWindow: MonthWindow;
  hiddenCategories: string[];
  hideEmpty: boolean;
};

export function sliceHeatmapView(view: HeatmapView, opts: SliceOptions): SlicedHeatmapView {
  const monthIdxs = filterMonthIndices(view.months, opts.monthWindow);
  const hidden = new Set(opts.hiddenCategories);

  let catIdxs = view.categories
    .map((name, i) => ({ name, i }))
    .filter(({ name }) => !hidden.has(name))
    .map(({ i }) => i);

  if (opts.hideEmpty) {
    catIdxs = catIdxs.filter((ci) => !categoryIsEmpty(view.values, monthIdxs, ci));
  }

  const months = monthIdxs.map((i) => view.months[i]);
  const categories = catIdxs.map((i) => view.categories[i]);

  const values = monthIdxs.map((ri) => catIdxs.map((ci) => view.values[ri]?.[ci] ?? null));
  const zPaint = monthIdxs.map((ri) => catIdxs.map((ci) => view.zPaint[ri]?.[ci] ?? null));

  const columnTotals: number[] = [];
  const columnAverages: number[] = [];
  const columnMedians: number[] = [];
  for (let c = 0; c < catIdxs.length; c++) {
    const col = values.map((row) => row[c]);
    columnTotals.push(sumFinite(col));
    columnAverages.push(meanFinite(col));
    columnMedians.push(medianFinite(col));
  }

  const rowTotals: number[] = [];
  const rowAverages: number[] = [];
  const rowMedians: number[] = [];
  for (let r = 0; r < monthIdxs.length; r++) {
    const row = values[r];
    rowTotals.push(sumFinite(row));
    rowAverages.push(meanFinite(row));
    rowMedians.push(medianFinite(row));
  }

  // YTD / 12M stay ledger-wide from the original rows (context, not visible-only).
  const rowYtdSums = monthIdxs.map((i) => view.rowYtdSums?.[i] ?? 0);
  const rowYtdAverages = monthIdxs.map((i) => view.rowYtdAverages?.[i] ?? 0);
  const rowRolling12Sums = monthIdxs.map((i) => view.rowRolling12Sums?.[i] ?? 0);
  const rowRolling12Averages = monthIdxs.map((i) => view.rowRolling12Averages?.[i] ?? 0);

  return {
    title: view.title,
    reportType: view.reportType,
    months,
    categories,
    values,
    zPaint,
    colorScale: view.colorScale,
    columnTotals,
    columnAverages,
    columnMedians,
    rowTotals,
    rowAverages,
    rowMedians,
    rowYtdSums,
    rowYtdAverages,
    rowRolling12Sums,
    rowRolling12Averages,
  };
}

export function axisStatsFromSlice(view: SlicedHeatmapView, axis: 'row' | 'col', index: number): AxisStats {
  if (axis === 'row') {
    return {
      total: view.rowTotals[index] ?? 0,
      avg: view.rowAverages[index] ?? 0,
      median: view.rowMedians[index] ?? 0,
    };
  }
  return {
    total: view.columnTotals[index] ?? 0,
    avg: view.columnAverages[index] ?? 0,
    median: view.columnMedians[index] ?? 0,
  };
}

/** Map month window prefs to drilldown period + optional ym range. */
export function drillPeriodFromWindow(
  window: MonthWindow,
  months: string[],
): { period: string; ymRange?: { startYm: string; endYm: string } } {
  if (window.kind === 'all') return { period: 'all' };
  if (window.kind === '12m') return { period: '12m' };
  if (window.kind === 'ytd') return { period: 'ytd' };
  if (window.kind === 'year' && window.year && /^\d{4}$/.test(window.year)) {
    return { period: window.year };
  }
  if (window.kind === 'custom' && window.startYm && window.endYm) {
    return {
      period: '12m',
      ymRange: { startYm: window.startYm, endYm: window.endYm },
    };
  }
  if (months.length) {
    const newest = months[0];
    const oldest = months[months.length - 1];
    if (ymOk(newest) && ymOk(oldest)) {
      return { period: '12m', ymRange: { startYm: oldest, endYm: newest } };
    }
  }
  return { period: '12m' };
}

export function availableYears(months: string[]): string[] {
  const ys = new Set<string>();
  for (const m of months) {
    if (ymOk(m)) ys.add(m.slice(0, 4));
  }
  return [...ys].sort((a, b) => b.localeCompare(a));
}
