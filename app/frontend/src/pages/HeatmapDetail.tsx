import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { fetchJson, patchJson } from '../lib/api';

import './HeatmapDetail.css';

const SAFE_PATCH_COLUMNS = new Set(['notes', 'קטגוריה', '4 ספרות', 'statement_month']);

const FINGERPRINT_COLUMNS = [
  'תאריך',
  'בחובה',
  'בזכות',
  'מקור עסקה',
  'פירוט נוסף',
  'תאור מורחב',
] as const;

const REKEY_PHRASE = 'REKEY';

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

type PatchErrPayload = {
  ok?: boolean;
  error?: string;
  message?: string;
};

function cellDisplay(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  return String(v);
}

function rowId(row: Record<string, unknown>): number | null {
  const v = row.id;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && /^\d+$/.test(v)) return Number(v);
  return null;
}

function visibleColumns(columns: string[]): string[] {
  return columns.filter((c) => c !== 'id' && c !== 'fingerprint');
}

async function patchLedger(
  rowId: number,
  patch: Record<string, unknown>,
  opts?: { confirmFingerprint?: boolean; phrase?: string },
): Promise<{ ok: boolean; status: number; message?: string; code?: string }> {
  const r = await patchJson<PatchErrPayload>('/heatmap/api/transaction', {
    id: rowId,
    patch,
    confirm_fingerprint_change: opts?.confirmFingerprint ?? false,
    confirm_fingerprint_phrase: opts?.phrase ?? '',
  });
  const payload = r.data;
  const msg =
    typeof payload.message === 'string'
      ? payload.message
      : !r.ok
        ? `HTTP ${r.status}`
        : undefined;
  if (!r.ok || payload.ok === false) {
    return {
      ok: false,
      status: r.status,
      message: msg || 'Save failed',
      code: typeof payload.error === 'string' ? payload.error : undefined,
    };
  }
  return { ok: true, status: r.status };
}

function normalizeSafePayload(column: string, raw: string): unknown | undefined {
  const s = raw;
  if (column === 'statement_month') {
    const t = s.trim();
    if (!t) return null;
    if (!/^\d{4}-\d{2}$/.test(t)) return undefined;
    const mo = Number(t.slice(5));
    if (mo < 1 || mo > 12) return undefined;
    return t;
  }
  if (column === 'notes' || column === '4 ספרות') {
    const t = s.trim();
    return t === '' ? null : s;
  }
  return s.trim();
}

type FpDraft = Record<(typeof FINGERPRINT_COLUMNS)[number], string>;

function draftFromRow(row: Record<string, unknown>): FpDraft {
  const rawDate = row['תאריך'];
  const ta =
    rawDate == null
      ? ''
      : typeof rawDate === 'string'
        ? rawDate.slice(0, 10)
        : String(rawDate).slice(0, 10);
  const bh = row['בחובה'];
  const bz = row['בזכות'];
  const readTxt = (k: string) => {
    const v = row[k];
    if (v == null) return '';
    return String(v).trim();
  };
  return {
    תאריך: ta,
    בחובה: bh == null ? '' : String(bh),
    בזכות: bz == null ? '' : String(bz),
    'מקור עסקה': readTxt('מקור עסקה'),
    'פירוט נוסף': readTxt('פירוט נוסף'),
    'תאור מורחב': readTxt('תאור מורחב'),
  };
}

function FingerprintModal({
  row,
  open,
  onClose,
  onSaved,
}: {
  row: Record<string, unknown> | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const rid = row ? rowId(row) : null;
  const [draft, setDraft] = useState<FpDraft>(() =>
    row
      ? draftFromRow(row)
      : {
          תאריך: '',
          בחובה: '',
          בזכות: '',
          'מקור עסקה': '',
          'פירוט נוסף': '',
          'תאור מורחב': '',
        },
  );
  const [understood, setUnderstood] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (open && row) {
      setDraft(draftFromRow(row));
      setUnderstood(false);
      setPhrase('');
      setErr('');
    }
  }, [open, row]);

  if (!open || rid == null) return null;

  const submit = async () => {
    setErr('');
    if (!understood) {
      setErr('Confirm that you understand this changes how duplicates match.');
      return;
    }
    if (phrase.trim() !== REKEY_PHRASE) {
      setErr(`Type ${REKEY_PHRASE} to confirm.`);
      return;
    }
    let bh: number;
    let bz: number;
    try {
      bh = Number(String(draft['בחובה']).trim());
      bz = Number(String(draft['בזכות']).trim());
      if (!Number.isFinite(bh) || !Number.isFinite(bz)) throw new Error('bad amount');
    } catch {
      setErr('בחובה and בזכות must be valid numbers.');
      return;
    }
    const patch: Record<string, unknown> = {
      תאריך: draft['תאריך'].trim(),
      בחובה: bh,
      בזכות: bz,
      'מקור עסקה': draft['מקור עסקה'].trim(),
      'פירוט נוסף': draft['פירוט נוסף'].trim(),
      'תאור מורחב': draft['תאור מורחב'].trim(),
    };
    setBusy(true);
    try {
      const out = await patchLedger(rid, patch, { confirmFingerprint: true, phrase });
      if (!out.ok) {
        setErr(out.message || 'Save failed');
        return;
      }
      await onSaved();
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const lbl = (k: string) => k;

  return (
    <div className="hm-detail-modal-overlay" role="presentation" onClick={busy ? undefined : onClose}>
      <div
        className="hm-detail-modal"
        role="dialog"
        aria-labelledby="hm-detail-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="hm-detail-modal-title" className="hm-detail-modal-title">
          עריכת זהות עסקה (משפיע על טביעת אצבע / התאמת ייבוא)
        </h2>
        <p className="hm-detail-modal-warn">
          שינוי התאריך, הסכומים או טקסט התיאור שמתוכם נגזרת טביעת האצבע עלול לשנות תוצאות איחוד והימנעות
          מכפילויות בעת ניפוי מאוחר. הקטגוריה וההערות לא משפיעים על טביעת האצבע.
        </p>
        <div className="hm-detail-modal-grid">
          {FINGERPRINT_COLUMNS.map((field) => (
            <label key={field} className="hm-detail-modal-field">
              <span>{lbl(field)}</span>
              {field === 'תאריך' ? (
                <input
                  type="text"
                  className="hm-detail-modal-input"
                  dir="ltr"
                  value={draft[field]}
                  onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
                  placeholder="YYYY-MM-DD"
                />
              ) : field === 'בחובה' || field === 'בזכות' ? (
                <input
                  type="number"
                  step="any"
                  className="hm-detail-modal-input"
                  dir="ltr"
                  value={draft[field]}
                  onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
                />
              ) : (
                <input
                  type="text"
                  className="hm-detail-modal-input"
                  value={draft[field]}
                  onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
                />
              )}
            </label>
          ))}
        </div>
        <label className="hm-detail-modal-check">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />{' '}
          אני מבין שהזיהוי הפנימי של הרשומה עלול להשתנות ויכול ליצור התנגשות עם תנועה קיימת.
        </label>
        <label className="hm-detail-modal-field">
          <span>אשר הקלדה: <code>{REKEY_PHRASE}</code></span>
          <input
            type="text"
            className="hm-detail-modal-input"
            dir="ltr"
            autoComplete="off"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
          />
        </label>
        {err ? <p className="hm-detail-modal-err">{err}</p> : null}
        <div className="hm-detail-modal-actions">
          <button type="button" className="secondary" onClick={onClose} disabled={busy}>
            ביטול
          </button>
          <button type="button" onClick={() => void submit()} disabled={busy}>
            {busy ? 'שומר…' : 'שמור שינוי זהות'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function HeatmapDetail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryString = useMemo(() => params.toString(), [params]);

  const [data, setData] = useState<HeatmapDetailResponse | null>(null);
  const [err, setErr] = useState<string>('');
  const [rowErr, setRowErr] = useState<Record<number, string>>({});
  const [fpRow, setFpRow] = useState<Record<string, unknown> | null>(null);
  const [excludingRowId, setExcludingRowId] = useState<number | null>(null);

  const loadDetail = useCallback(async (opts?: { preserveExisting?: boolean }) => {
    if (!queryString) {
      setErr('Missing query parameters.');
      setData(null);
      return;
    }
    setErr('');
    if (!opts?.preserveExisting) {
      setData(null);
    }
    try {
      const r = await fetchJson<HeatmapDetailResponse>(`/heatmap/api/detail?${queryString}`, {
        cache: 'no-store',
      });
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
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    }
  }, [queryString]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const saveSafeCell = async (row: Record<string, unknown>, column: string, raw: string) => {
    const id = rowId(row);
    if (id == null) return;
    const normalized = normalizeSafePayload(column, raw);
    if (normalized === undefined) {
      setRowErr((m) => ({
        ...m,
        [id]: column === 'statement_month' ? 'statement_month must be YYYY-MM or empty.' : 'Invalid value.',
      }));
      return;
    }
    const prev = row[column];
    const prevStr =
      prev == null ? '' : typeof prev === 'number' && Number.isFinite(prev) ? String(prev) : String(prev);
    const nextStr =
      normalized === null ? '' : typeof normalized === 'number' ? String(normalized) : String(normalized);
    if (prevStr.trim() === nextStr.trim()) {
      setRowErr((m) => {
        const next = { ...m };
        delete next[id];
        return next;
      });
      return;
    }

    const out = await patchLedger(id, { [column]: normalized });
    if (!out.ok) {
      setRowErr((m) => ({ ...m, [id]: out.message || 'Save failed' }));
      return;
    }
    setRowErr((m) => {
      const next = { ...m };
      delete next[id];
      return next;
    });
    await loadDetail({ preserveExisting: true });
  };

  const excludeFromCalculations = async (row: Record<string, unknown>) => {
    const id = rowId(row);
    if (id == null || excludingRowId != null) return;
    setRowErr((m) => {
      const next = { ...m };
      delete next[id];
      return next;
    });
    setExcludingRowId(id);
    try {
      const out = await patchLedger(id, { excluded_from_calculations: 1 });
      if (!out.ok) {
        setRowErr((m) => ({ ...m, [id]: out.message || 'Exclude failed' }));
        return;
      }
      await loadDetail({ preserveExisting: true });
    } finally {
      setExcludingRowId(null);
    }
  };

  const fpModalOpen = fpRow != null;

  return (
    <div className="hm-detail-page">
      <FingerprintModal
        row={fpRow}
        open={fpModalOpen}
        onClose={() => setFpRow(null)}
        onSaved={() => loadDetail({ preserveExisting: true })}
      />
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
          {(data.sections ?? []).map((sec, si) => {
            const cols = visibleColumns(sec.columns);
            return (
              <section key={si} className="hm-detail-section">
                {sec.subtitle ? <h2 className="hm-detail-sub">{sec.subtitle}</h2> : null}
                {!sec.rows.length ? (
                  <p className="hm-detail-empty">אין נתונים</p>
                ) : (
                  <div className="hm-detail-table-wrap">
                    <table className="hm-detail-table">
                      <thead>
                        <tr>
                          {cols.map((c) => (
                            <th key={c}>{c}</th>
                          ))}
                          <th className="hm-detail-actions-th">פעולות</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sec.rows.map((row, ri) => {
                          const rid = rowId(row);
                          const rk = rid ?? ri;
                          const re = rid != null ? rowErr[rid] : undefined;
                          const rowRefreshKey = `${rk}-${String(row['data_updated_at'] ?? '')}-${String(row['category_updated_at'] ?? '')}`;
                          return (
                            <Fragment key={rowRefreshKey}>
                              <tr>
                                {cols.map((c) => (
                                  <td key={c}>
                                    {rid != null && SAFE_PATCH_COLUMNS.has(c) ? (
                                      c === 'notes' ? (
                                        <textarea
                                          className="hm-detail-cell-input hm-detail-cell-notes"
                                          dir="auto"
                                          defaultValue={
                                            row[c] == null
                                              ? ''
                                              : typeof row[c] === 'string'
                                                ? row[c]
                                                : String(row[c])
                                          }
                                          onBlur={(e) => void saveSafeCell(row, c, e.target.value)}
                                        />
                                      ) : (
                                        <input
                                          type="text"
                                          className="hm-detail-cell-input"
                                          dir={c === 'statement_month' ? 'ltr' : 'auto'}
                                          defaultValue={
                                            row[c] == null
                                              ? ''
                                              : typeof row[c] === 'string'
                                                ? row[c]
                                                : String(row[c])
                                          }
                                          onBlur={(e) => void saveSafeCell(row, c, e.target.value)}
                                        />
                                      )
                                    ) : (
                                      cellDisplay(row[c])
                                    )}
                                  </td>
                                ))}
                                <td className="hm-detail-actions-td">
                                  {rid != null ? (
                                    <div className="hm-detail-actions-stack">
                                      <button
                                        type="button"
                                        className="secondary hm-detail-rekey"
                                        title="עריכת שדות שמשפיעים על טביעת האצבע"
                                        disabled={excludingRowId === rid}
                                        onClick={() => setFpRow(row)}
                                      >
                                        זהות…
                                      </button>
                                      <button
                                        type="button"
                                        className="secondary hm-detail-exclude"
                                        title="Row stays in DB but is omitted from heatmaps, KPIs, and categorize queue. Restore from Data Integrity."
                                        disabled={excludingRowId != null}
                                        onClick={() => void excludeFromCalculations(row)}
                                      >
                                        {excludingRowId === rid ? '…' : 'החרג מהחישוב'}
                                      </button>
                                    </div>
                                  ) : null}
                                </td>
                              </tr>
                              {re ? (
                                <tr className="hm-detail-row-err-tr">
                                  <td className="hm-detail-row-err" colSpan={cols.length + 1}>
                                    {re}
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}
