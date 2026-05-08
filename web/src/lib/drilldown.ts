export type HeatmapReportType = 'expense' | 'income' | 'net';

export function heatmapDetailCell(type: HeatmapReportType, ym: string, cat: string): string {
  const p = new URLSearchParams();
  p.set('type', type);
  p.set('ym', ym);
  p.set('cat', cat);
  return `/heatmap/detail?${p.toString()}`;
}

export function heatmapDetailMonth(type: HeatmapReportType, ym: string): string {
  const p = new URLSearchParams();
  p.set('type', type);
  p.set('ym', ym);
  return `/heatmap/detail?${p.toString()}`;
}

export function heatmapDetailCategory(type: HeatmapReportType, category: string, period = '12m'): string {
  const p = new URLSearchParams();
  p.set('type', type);
  p.set('cat', category);
  p.set('period', period);
  return `/heatmap/detail?${p.toString()}`;
}

export function heatmapDetailSource(source: string, months = 12): string {
  const p = new URLSearchParams();
  p.set('src', source);
  p.set('months', String(months));
  return `/heatmap/detail?${p.toString()}`;
}
