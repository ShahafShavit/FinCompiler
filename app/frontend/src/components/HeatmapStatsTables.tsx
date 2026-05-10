import type { CSSProperties } from 'react';

import { statsColumnStyles, type ReportType } from '../lib/heatmapColors';
import { formatCellMoney } from '../lib/heatmapFormat';

export type StatsTabular = {
  columns: string[];
  rows: Record<string, number | string | null>[];
};

function StatsTable({
  reportType,
  tabular,
  indexLabel,
}: {
  reportType: ReportType;
  tabular: StatsTabular;
  indexLabel: string;
}) {
  const { columns, rows } = tabular;
  if (!columns.length || !rows.length) {
    return <p className="no-data">אין נתונים</p>;
  }

  const dataCols = columns.filter((c) => c !== indexLabel);
  const styleByCol: Record<string, { bg: string; fg: string }[]> = {};
  for (const col of dataCols) {
    const vals = rows.map((row) => {
      const x = row[col];
      if (typeof x === 'number') return x;
      if (x == null) return 0;
      const n = Number(x);
      return Number.isFinite(n) ? n : 0;
    });
    styleByCol[col] = statsColumnStyles(vals, reportType, col);
  }

  return (
    <table className="styled-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => (
          <tr key={ri}>
            {columns.map((col) => {
              const raw = row[col];
              if (col === indexLabel) {
                return <td key={col}>{String(raw ?? '')}</td>;
              }
              const num =
                typeof raw === 'number' ? raw : raw == null ? NaN : Number(raw);
              const display = Number.isFinite(num) ? formatCellMoney(num) : '—';
              const st = styleByCol[col]?.[ri];
              const cellStyle: CSSProperties | undefined =
                st?.bg && st.bg !== ''
                  ? {
                      backgroundColor: st.bg,
                      color: st.fg,
                      textShadow: 'none',
                    }
                  : {
                      color: '#e8e8ec',
                    };
              return (
                <td key={col} style={cellStyle}>
                  {display}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function HeatmapStatsTables({
  reportType,
  byCategory,
  byMonth,
}: {
  reportType: ReportType;
  byCategory: StatsTabular;
  byMonth: StatsTabular;
}) {
  return (
    <>
      <div className="stats-table-container">
        <h2>סיכום לפי קטגוריה</h2>
        <StatsTable reportType={reportType} tabular={byCategory} indexLabel="קטגוריה" />
      </div>
      <div className="stats-table-container">
        <h2>סיכום לפי חודש</h2>
        <StatsTable reportType={reportType} tabular={byMonth} indexLabel="חודש" />
      </div>
    </>
  );
}
