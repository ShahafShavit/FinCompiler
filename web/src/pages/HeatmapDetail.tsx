import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { fetchJson } from '../lib/api';

import './HeatmapDetail.css';

export type HeatmapDetailSection = {
  subtitle: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
};

type HeatmapDetailResponse = {
  ok: boolean;
  title?: string;
  sections?: HeatmapDetailSection[];
  error?: string;
  message?: string;
};

function cellDisplay(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  return String(v);
}

export default function HeatmapDetail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryString = useMemo(() => params.toString(), [params]);

  const [data, setData] = useState<HeatmapDetailResponse | null>(null);
  const [err, setErr] = useState<string>('');

  useEffect(() => {
    if (!queryString) {
      setErr('Missing query parameters.');
      setData(null);
      return;
    }
    let cancelled = false;
    setErr('');
    setData(null);
    void (async () => {
      try {
        const r = await fetchJson<HeatmapDetailResponse>(
          `/heatmap/api/detail?${queryString}`,
          { cache: 'no-store' },
        );
        if (cancelled) return;
        if (!r.ok || !r.data.ok) {
          const msg =
            r.data.message ||
            r.data.error ||
            (r.status === 404 ? 'No matching transactions.' : `HTTP ${r.status}`);
          setErr(msg);
          setData(null);
          return;
        }
        setData(r.data);
        setErr('');
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
          setData(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [queryString]);

  return (
    <div className="hm-detail-page">
      <div className="hm-detail-toolbar">
        <button type="button" className="secondary hm-detail-back" onClick={() => navigate('/heatmap')}>
          ← Heatmap
        </button>
        <button type="button" className="secondary hm-detail-back" onClick={() => navigate(-1)}>
          Back
        </button>
      </div>
      {!queryString ? (
        <p className="hm-detail-empty">No drill-down parameters in the URL.</p>
      ) : err ? (
        <p className="hm-detail-error">{err}</p>
      ) : !data?.title ? (
        <p className="hm-detail-empty">Loading…</p>
      ) : (
        <>
          <h1 className="hm-detail-title">{data.title}</h1>
          {(data.sections ?? []).map((sec, si) => (
            <section key={si} className="hm-detail-section">
              {sec.subtitle ? <h2 className="hm-detail-sub">{sec.subtitle}</h2> : null}
              {!sec.rows.length ? (
                <p className="hm-detail-empty">אין נתונים</p>
              ) : (
                <div className="hm-detail-table-wrap">
                  <table className="hm-detail-table">
                    <thead>
                      <tr>
                        {sec.columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sec.rows.map((row, ri) => (
                        <tr key={ri}>
                          {sec.columns.map((c) => (
                            <td key={c}>{cellDisplay(row[c])}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          ))}
        </>
      )}
    </div>
  );
}
