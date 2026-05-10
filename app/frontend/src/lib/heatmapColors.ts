import {
  interpolateGreens,
  interpolateOranges,
  interpolatePurples,
  interpolateRdBu,
  interpolateReds,
} from 'd3-scale-chromatic';

export type HeatmapScheme = 'Reds' | 'Greens' | 'RdBu';

function interpolateForScheme(scheme: HeatmapScheme): (t: number) => string {
  switch (scheme) {
    case 'Reds':
      return interpolateReds;
    case 'Greens':
      return interpolateGreens;
    case 'RdBu':
      return interpolateRdBu;
    default:
      return interpolateReds;
  }
}

/** Matplotlib ``Normalize`` / ``TwoSlopeNorm``-style t ∈ [0,1]. */
function normScalar(
  v: number,
  vmin: number,
  vmax: number,
  center: number | null,
): number | null {
  if (!Number.isFinite(v)) return null;
  if (center != null && vmin < center && center < vmax) {
    if (v < center) {
      const t = (v - vmin) / (center - vmin);
      return Math.min(1, Math.max(0, 0.5 * t));
    }
    const t = (v - center) / (vmax - center);
    return Math.min(1, Math.max(0, 0.5 + 0.5 * t));
  }
  if (!Number.isFinite(vmin) || !Number.isFinite(vmax)) return null;
  if (vmax === vmin) return 0.5;
  const t = (v - vmin) / (vmax - vmin);
  return Math.min(1, Math.max(0, t));
}

function fgForBg(bgHex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(bgHex.trim());
  if (!m) return '#f4f6fb';
  const n = parseInt(m[1], 16);
  const r = ((n >> 16) & 255) / 255;
  const g = ((n >> 8) & 255) / 255;
  const b = (n & 255) / 255;
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.58 ? '#111318' : '#f4f6fb';
}

/** Cell backgrounds from paint matrix + scale metadata (replaces server matplotlib). */
export function heatmapGridColors(
  zPaint: (number | null)[][],
  scheme: HeatmapScheme,
  center: number | null,
): { cellBg: string[][]; cellFg: string[][] } {
  const flat = zPaint.flat().filter((x): x is number => x != null && Number.isFinite(x));
  if (flat.length === 0) {
    const dead = '#333337';
    const cellBg = zPaint.map((row) => row.map(() => dead));
    const cellFg = zPaint.map((row) => row.map(() => '#f4f6fb'));
    return { cellBg, cellFg };
  }
  const vmin = Math.min(...flat);
  const vmax = Math.max(...flat);
  const interp = interpolateForScheme(scheme);
  const useCenter =
    center != null && Number.isFinite(center) && vmin < center && center < vmax;

  const cellBg = zPaint.map((row) =>
    row.map((v) => {
      if (v == null || !Number.isFinite(v)) return '#333337';
      const t = normScalar(v, vmin, vmax, useCenter ? center : null);
      if (t == null) return '#333337';
      return interp(t);
    }),
  );
  const cellFg = cellBg.map((row) => row.map((bg) => fgForBg(bg)));
  return { cellBg, cellFg };
}

export type ReportType = 'expense' | 'income' | 'net';

const SEQ_COL_NAMES = [
  'סך הכל (Total)',
  'ממוצע חודשי (Avg)',
  'ממוצע לקטגוריה (Avg)',
  'חציון (Median)',
  'מקסימום (Max)',
  'מינימום (Min)',
  'אחוזון 75 (75th Pctl)',
  'אחוזון 25 (25th Pctl)',
] as const;

const STD_COL = 'סטיית תקן (Std Dev)';
const COUNT_COL = 'ספירה (Count > 0)';

function transformStat(v: number, reportType: ReportType): number {
  if (reportType === 'net') {
    return Math.sign(v) * Math.log1p(Math.abs(v));
  }
  return Math.log1p(Math.max(v, 0));
}

/** Per-cell styles for one stats column (pandas Styler column-wise). */
export function statsColumnStyles(
  values: number[],
  reportType: ReportType,
  columnName: string,
): { bg: string; fg: string }[] {
  const isSeq = (SEQ_COL_NAMES as readonly string[]).includes(columnName);
  const isStd = columnName === STD_COL;
  const isCount = columnName === COUNT_COL;
  if (!isSeq && !isStd && !isCount) {
    return values.map(() => ({ bg: 'transparent', fg: '#e8e8ec' }));
  }

  let cmap: (t: number) => string = interpolateReds;
  if (isStd) cmap = interpolateOranges;
  else if (isCount) cmap = interpolatePurples;
  else if (reportType === 'net') cmap = interpolateRdBu;
  else if (reportType === 'income') cmap = interpolateGreens;
  else cmap = interpolateReds;

  const transformed = values.map((v) => transformStat(v, reportType));
  const minT = Math.min(...transformed);
  const maxT = Math.max(...transformed);
  if (!Number.isFinite(minT) || !Number.isFinite(maxT) || minT === maxT) {
    return values.map(() => ({ bg: '', fg: '#e8e8ec' }));
  }

  return transformed.map((tv) => {
    const t = (tv - minT) / (maxT - minT);
    const clamped = Math.min(1, Math.max(0, t));
    const bg = cmap(clamped);
    return { bg, fg: fgForBg(bg) };
  });
}
