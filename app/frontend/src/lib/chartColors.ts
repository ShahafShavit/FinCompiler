/** Shared chart/series colors for Dashboard + Portfolio (warmer teal family). */

const TEAL_FAMILY = [
  '#3db89a',
  '#5b8def',
  '#e8b84d',
  '#e879a8',
  '#7bc96f',
  '#9b7bff',
  '#40c0c8',
  '#f0a070',
];

export function seriesColor(index: number): string {
  return TEAL_FAMILY[index % TEAL_FAMILY.length];
}

/** Stable hue from string (activity name, ticker, etc.). */
export function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue} 48% 58%)`;
}

export const CHART_INCOME = '#6bcf8e';
export const CHART_EXPENSE = '#e87a7a';
export const CHART_NET = '#5b8def';
export const CHART_ACCENT = '#3db89a';
