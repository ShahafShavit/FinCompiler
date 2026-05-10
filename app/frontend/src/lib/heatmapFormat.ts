/** Mirrors ``web_control.heatmap._format_cell_money`` (Python). */
export function formatCellMoney(v: number): string {
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  if (Math.abs(a - Math.round(a)) < 0.01) {
    return `${sign}${a.toLocaleString('en-US', { maximumFractionDigits: 0, minimumFractionDigits: 0 })}₪`;
  }
  return `${sign}${a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}₪`;
}
