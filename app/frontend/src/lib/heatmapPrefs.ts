/** Persisted heatmap viewing preferences (localStorage). */

export type ReportType = 'expense' | 'income' | 'net';

export type MetricKey = 'rowSum' | 'rowAvg' | 'ytdSum' | 'ytdAvg' | 'l12Sum' | 'l12Avg';

export const METRIC_ORDER: MetricKey[] = [
  'rowSum',
  'rowAvg',
  'ytdSum',
  'ytdAvg',
  'l12Sum',
  'l12Avg',
];

export type Density = 'compact' | 'comfortable';
export type Zoom = '90' | '100' | '115';

export type MonthWindowKind = 'all' | '12m' | 'ytd' | 'year' | 'custom';

export type MonthWindow = {
  kind: MonthWindowKind;
  year?: string;
  startYm?: string;
  endYm?: string;
};

export type HeatmapPrefs = {
  reportType: ReportType;
  hiddenMetrics: MetricKey[];
  hiddenCategories: string[];
  hideEmpty: boolean;
  monthWindow: MonthWindow;
  density: Density;
  zoom: Zoom;
};

export const DEFAULT_HEATMAP_PREFS: HeatmapPrefs = {
  reportType: 'expense',
  hiddenMetrics: [],
  hiddenCategories: [],
  hideEmpty: false,
  monthWindow: { kind: 'all' },
  density: 'comfortable',
  zoom: '100',
};

const STORAGE_KEY = 'fincompiler.heatmap.view';

function isMetricKey(x: unknown): x is MetricKey {
  return typeof x === 'string' && (METRIC_ORDER as string[]).includes(x);
}

function isReportType(x: unknown): x is ReportType {
  return x === 'expense' || x === 'income' || x === 'net';
}

function isMonthWindow(x: unknown): x is MonthWindow {
  if (!x || typeof x !== 'object') return false;
  const k = (x as MonthWindow).kind;
  return k === 'all' || k === '12m' || k === 'ytd' || k === 'year' || k === 'custom';
}

export function loadHeatmapPrefs(): HeatmapPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_HEATMAP_PREFS, monthWindow: { ...DEFAULT_HEATMAP_PREFS.monthWindow } };
    const parsed = JSON.parse(raw) as Partial<HeatmapPrefs>;
    const hiddenMetrics = Array.isArray(parsed.hiddenMetrics)
      ? parsed.hiddenMetrics.filter(isMetricKey)
      : [];
    const hiddenCategories = Array.isArray(parsed.hiddenCategories)
      ? parsed.hiddenCategories.filter((c): c is string => typeof c === 'string')
      : [];
    return {
      reportType: isReportType(parsed.reportType) ? parsed.reportType : DEFAULT_HEATMAP_PREFS.reportType,
      hiddenMetrics,
      hiddenCategories,
      hideEmpty: Boolean(parsed.hideEmpty),
      monthWindow: isMonthWindow(parsed.monthWindow)
        ? {
            kind: parsed.monthWindow.kind,
            year: parsed.monthWindow.year,
            startYm: parsed.monthWindow.startYm,
            endYm: parsed.monthWindow.endYm,
          }
        : { ...DEFAULT_HEATMAP_PREFS.monthWindow },
      density: parsed.density === 'compact' ? 'compact' : 'comfortable',
      zoom: parsed.zoom === '90' || parsed.zoom === '115' ? parsed.zoom : '100',
    };
  } catch {
    return { ...DEFAULT_HEATMAP_PREFS, monthWindow: { ...DEFAULT_HEATMAP_PREFS.monthWindow } };
  }
}

export function saveHeatmapPrefs(prefs: HeatmapPrefs): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore quota / private mode */
  }
}

export function visibleMetrics(hidden: MetricKey[]): MetricKey[] {
  return METRIC_ORDER.filter((m) => !hidden.includes(m));
}
