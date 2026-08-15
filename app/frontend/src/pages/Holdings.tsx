import { useCallback, useEffect, useMemo, useState } from 'react';

import PageShell from '../components/PageShell';
import { fetchJson } from '../lib/api';
import { he } from '../lib/he';

import './Holdings.css';

type TimelineRow = {
  as_of_date: string;
  activity_type: string;
  balance_ils: number;
};

type Meta = {
  row_count?: number;
  date_count?: number;
  min_date?: string;
  max_date?: string;
  activity_types?: string[];
};

/** One pivot row while editing — React state is source of truth for save. */
type TlEditRow = {
  originalDate: string;
  asOfDate: string;
  cells: Record<string, string>;
};

function parseLooseNumber(v: unknown): number {
  const s = String(v == null ? '' : v)
    .trim()
    .replace(/,/g, '');
  if (s === '') return 0;
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

function fmtNum(n: number): string {
  return n.toLocaleString('he-IL', { maximumFractionDigits: 2 });
}

function buildEditRows(
  dates: string[],
  acts: string[],
  byDate: Record<string, Record<string, number>>,
): TlEditRow[] {
  return dates.map((d) => {
    const cells: Record<string, string> = {};
    for (const a of acts) {
      const v = Number((byDate[d] && byDate[d][a]) || 0);
      cells[a] = Number.isFinite(v) ? String(v) : '0';
    }
    return { originalDate: d, asOfDate: d, cells };
  });
}

export default function Holdings() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rowsFlat, setRowsFlat] = useState<TimelineRow[]>([]);
  const [snapshotRows, setSnapshotRows] = useState<TimelineRow[]>([]);
  const [timelineEditing, setTimelineEditing] = useState(false);
  const [editRows, setEditRows] = useState<TlEditRow[]>([]);
  const [editActs, setEditActs] = useState<string[]>([]);

  const [tlFrom, setTlFrom] = useState('');
  const [tlTo, setTlTo] = useState('');
  const [selectedActs, setSelectedActs] = useState<string[]>([]);

  const [manualMode, setManualMode] = useState<'manual' | 'paste'>('manual');
  const [mDate, setMDate] = useState('');
  const [activityRows, setActivityRows] = useState<{ activity: string; balance: string }[]>([
    { activity: '', balance: '' },
    { activity: '', balance: '' },
  ]);

  const [pasteText, setPasteText] = useState('');
  const [pasteParseHint, setPasteParseHint] = useState('');
  const [parsedPasteRows, setParsedPasteRows] = useState<TimelineRow[]>([]);
  const [ingestStatus, setIngestStatus] = useState('');

  const loadMeta = useCallback(async () => {
    const r = await fetchJson<Meta>('/api/holdings/meta', { cache: 'no-store' });
    const m = r.data || {};
    setMeta(m);
    const acts = m.activity_types || [];
    setSelectedActs((prev) => (prev.length ? prev : [...acts]));
  }, []);

  const loadTimeline = useCallback(async () => {
    const p = new URLSearchParams();
    if (tlFrom) p.set('from', tlFrom);
    if (tlTo) p.set('to', tlTo);
    selectedActs.forEach((a) => p.append('activity', a));
    const r = await fetchJson<{ rows?: TimelineRow[] }>(
      `/api/holdings/timeline?${p.toString()}`,
      { cache: 'no-store' },
    );
    const rowList = (r.data && r.data.rows) || [];
    setRowsFlat(rowList);
    setSnapshotRows(rowList);
    setTimelineEditing(false);
    setEditRows([]);
    setEditActs([]);
  }, [tlFrom, tlTo, selectedActs]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    if (!meta) return;
    const acts = meta.activity_types || [];
    if (selectedActs.length === 0 && acts.length) setSelectedActs([...acts]);
  }, [meta, selectedActs.length]);

  useEffect(() => {
    if (selectedActs.length > 0) void loadTimeline();
  }, [selectedActs, loadTimeline]);

  const pivot = useMemo(() => {
    if (!rowsFlat.length) {
      return { acts: [] as string[], dates: [] as string[], byDate: {} as Record<string, Record<string, number>> };
    }
    const acts = Array.from(new Set(rowsFlat.map((r) => r.activity_type))).sort();
    const byDate: Record<string, Record<string, number>> = {};
    rowsFlat.forEach((r) => {
      const d = r.as_of_date;
      if (!byDate[d]) byDate[d] = {};
      byDate[d][r.activity_type] = Number(r.balance_ils || 0);
    });
    const dates = Object.keys(byDate).sort();
    return { acts, dates, byDate };
  }, [rowsFlat]);

  const snapshotPivot = useMemo(() => {
    if (!snapshotRows.length) {
      return { byDate: {} as Record<string, Record<string, number>> };
    }
    const byDate: Record<string, Record<string, number>> = {};
    snapshotRows.forEach((r) => {
      const d = r.as_of_date;
      if (!byDate[d]) byDate[d] = {};
      byDate[d][r.activity_type] = Number(r.balance_ils || 0);
    });
    return { byDate };
  }, [snapshotRows]);

  const displayActs = timelineEditing ? editActs : pivot.acts;
  const displayRows: TlEditRow[] = timelineEditing
    ? editRows
    : buildEditRows(pivot.dates, pivot.acts, pivot.byDate);

  const summaryLine = useMemo(() => {
    const { acts, dates, byDate } = pivot;
    if (!dates.length) return '';
    const latest = dates[dates.length - 1];
    const prev = dates.length > 1 ? dates[dates.length - 2] : null;
    const totalLatest = acts.reduce((acc, a) => acc + Number((byDate[latest] && byDate[latest][a]) || 0), 0);
    const totalPrev = prev
      ? acts.reduce((acc, a) => acc + Number((byDate[prev] && byDate[prev][a]) || 0), 0)
      : 0;
    const delta = totalLatest - totalPrev;
    return (
      `${he.holdings.latestTotal} (${latest}): ${fmtNum(totalLatest)}` +
      (prev ? ` · ${he.holdings.deltaVs} ${prev}: ${fmtNum(delta)}` : '')
    );
  }, [pivot]);

  const hasTimelineChanges = useMemo(() => {
    if (!timelineEditing) return false;
    for (const row of editRows) {
      const asOf = (row.asOfDate || '').trim();
      if (!asOf || asOf !== row.originalDate) return true;
      const origCells = snapshotPivot.byDate[row.originalDate] || {};
      for (const act of editActs) {
        const orig = parseLooseNumber(origCells[act] ?? 0);
        const cur = parseLooseNumber(row.cells[act]);
        if (!Number.isFinite(cur)) return true;
        if (Math.abs(cur - orig) > 0.000001) return true;
      }
    }
    return false;
  }, [timelineEditing, editRows, editActs, snapshotPivot]);

  const saveDisabled = !timelineEditing || !hasTimelineChanges;

  function timelineEditedRowsPayload(): TimelineRow[] {
    const rows: TimelineRow[] = [];
    const seen = new Set<string>();
    for (const row of editRows) {
      const asOfDate = String(row.asOfDate || '').trim();
      if (!asOfDate) throw new Error('Date is required for all timeline rows.');
      for (const activityType of editActs) {
        const raw = String(row.cells[activityType] ?? '')
          .trim()
          .replace(/,/g, '');
        if (raw === '') continue;
        const val = Number(raw);
        if (!Number.isFinite(val)) {
          throw new Error(`Invalid number for ${asOfDate} / ${activityType}: ${raw}`);
        }
        const key = `${asOfDate}||${activityType}`;
        if (seen.has(key)) continue;
        seen.add(key);
        rows.push({ as_of_date: asOfDate, activity_type: activityType, balance_ils: val });
      }
    }
    return rows;
  }

  function timelineDateMovesPayload(): { source_date: string; target_date: string }[] {
    const moves: { source_date: string; target_date: string }[] = [];
    for (const row of editRows) {
      const fromDate = String(row.originalDate || '').trim();
      const toDate = String(row.asOfDate || '').trim();
      if (!fromDate || !toDate || fromDate === toDate) continue;
      moves.push({ source_date: fromDate, target_date: toDate });
    }
    return moves;
  }

  function setEditDate(rowIdx: number, value: string) {
    setEditRows((rows) => rows.map((r, i) => (i === rowIdx ? { ...r, asOfDate: value } : r)));
  }

  function setEditCell(rowIdx: number, activity: string, value: string) {
    setEditRows((rows) =>
      rows.map((r, i) =>
        i === rowIdx ? { ...r, cells: { ...r.cells, [activity]: value } } : r,
      ),
    );
  }

  function startEdit() {
    setSnapshotRows([...rowsFlat]);
    setEditActs([...pivot.acts]);
    setEditRows(buildEditRows(pivot.dates, pivot.acts, pivot.byDate));
    setTimelineEditing(true);
  }

  function cancelEdit() {
    setRowsFlat([...snapshotRows]);
    setEditRows([]);
    setEditActs([]);
    setTimelineEditing(false);
  }

  async function moveTimelineDate(sourceDate: string, targetDate: string): Promise<Record<string, unknown>> {
    const firstTry = await fetchJson('/api/holdings/move-date', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_date: sourceDate, target_date: targetDate, overwrite_conflicts: false }),
    });
    if (firstTry.ok) return (firstTry.data || {}) as Record<string, unknown>;
    const conflicts = (firstTry.data as { conflicts?: unknown[] })?.conflicts || [];
    if (firstTry.status !== 409 || conflicts.length === 0) {
      throw new Error((firstTry.data as { message?: string })?.message || 'Failed to move date.');
    }
    const msg = `Changing date ${sourceDate} -> ${targetDate} has ${conflicts.length} conflict(s). Overwrite target date values?`;
    if (!window.confirm(msg)) throw new Error('Cancelled by user.');
    const overwrite = await fetchJson('/api/holdings/move-date', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_date: sourceDate, target_date: targetDate, overwrite_conflicts: true }),
    });
    if (!overwrite.ok) {
      throw new Error((overwrite.data as { message?: string })?.message || 'Failed to move date with overwrite.');
    }
    return (overwrite.data || {}) as Record<string, unknown>;
  }

  async function previewAndSaveRows(rows: TimelineRow[]) {
    if (!rows.length) {
      setIngestStatus('No rows to submit.');
      return;
    }
    const conflictRes = await fetchJson<{ conflicts?: unknown[] }>('/api/holdings/check-conflicts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows }),
    });
    const conflicts = (conflictRes.data && conflictRes.data.conflicts) || [];
    if (conflicts.length > 0) {
      if (!window.confirm(`Found ${conflicts.length} conflict(s). Overwrite existing values?`)) {
        setIngestStatus(JSON.stringify({ ok: false, message: 'Cancelled by user.', conflicts }, null, 2));
        return;
      }
    }
    const saveRes = await fetchJson('/api/holdings/manual-upsert-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows, overwrite_conflicts: true }),
    });
    setIngestStatus(JSON.stringify(saveRes.data ?? {}, null, 2));
    await loadMeta();
    await loadTimeline();
  }

  function manualRowsPayload(): TimelineRow[] {
    const rows: TimelineRow[] = [];
    for (const r of activityRows) {
      const activity = (r.activity || '').trim();
      const balance = (r.balance || '').trim();
      if (!activity || balance === '') continue;
      rows.push({ as_of_date: mDate, activity_type: activity, balance_ils: parseLooseNumber(balance) });
    }
    return rows;
  }

  return (
    <PageShell title={he.holdings.title} sub={he.holdings.sub} width="default" className="holdings-page">
      <section className="card holdings-timeline" aria-labelledby="holdings-timeline-heading">
        <h2 id="holdings-timeline-heading">{he.holdings.timeline}</h2>
        <div className="holdings-toolbar">
          <label className="holdings-field">
            {he.holdings.from}
            <input type="date" value={tlFrom} onChange={(e) => setTlFrom(e.target.value)} />
          </label>
          <label className="holdings-field">
            {he.holdings.to}
            <input type="date" value={tlTo} onChange={(e) => setTlTo(e.target.value)} />
          </label>
          <label className="holdings-field holdings-field--acts">
            {he.holdings.activities}
            <select
              multiple
              size={4}
              value={selectedActs}
              onChange={(e) => {
                const opts = Array.from(e.target.selectedOptions).map((o) => o.value);
                setSelectedActs(opts);
              }}
            >
              {(meta?.activity_types || []).map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <div className="holdings-toolbar__actions">
            <button type="button" onClick={() => void loadTimeline()}>
              {he.holdings.reload}
            </button>
            <button type="button" className="secondary" disabled={timelineEditing} onClick={startEdit}>
              {he.holdings.edit}
            </button>
            <button
              type="button"
              disabled={saveDisabled}
              onClick={async () => {
                try {
                  for (const mv of timelineDateMovesPayload()) {
                    await moveTimelineDate(mv.source_date, mv.target_date);
                  }
                  const rows = timelineEditedRowsPayload();
                  await previewAndSaveRows(rows);
                  setTimelineEditing(false);
                  setEditRows([]);
                  setEditActs([]);
                } catch (e) {
                  setIngestStatus(`Error: ${e}`);
                }
              }}
            >
              {he.holdings.save}
            </button>
            <button type="button" className="secondary" disabled={!timelineEditing} onClick={cancelEdit}>
              {he.holdings.cancel}
            </button>
          </div>
        </div>
        <p className="hint holdings-meta">
          {he.holdings.rows}: {meta?.row_count ?? 0} · {he.holdings.dates}: {meta?.date_count ?? 0} ·{' '}
          {he.holdings.range}: {meta?.min_date ?? '—'} .. {meta?.max_date ?? '—'}
        </p>
        {summaryLine ? <div className="hint holdings-summary">{summaryLine}</div> : null}
        <div className="holdings-scroll">
          <table className="holdings-tl">
            <thead>
              <tr>
                <th>{he.holdings.date}</th>
                {displayActs.map((a) => (
                  <th key={a}>{a}</th>
                ))}
                <th>{he.holdings.total}</th>
              </tr>
            </thead>
            <tbody>
              {displayRows.length === 0 ? (
                <tr>
                  <td colSpan={Math.max(2, displayActs.length + 2)}>{he.holdings.empty}</td>
                </tr>
              ) : (
                displayRows.map((row, rowIdx) => {
                  let rowTotal = 0;
                  displayActs.forEach((a) => {
                    const n = parseLooseNumber(row.cells[a]);
                    if (Number.isFinite(n)) rowTotal += n;
                  });
                  return (
                    <tr key={row.originalDate || `row-${rowIdx}`}>
                      <td>
                        <input
                          className="tl_date_input"
                          type="date"
                          value={row.asOfDate}
                          readOnly={!timelineEditing}
                          onChange={(e) => setEditDate(rowIdx, e.target.value)}
                        />
                      </td>
                      {displayActs.map((a) => (
                        <td key={a}>
                          <input
                            className="tl_cell_input"
                            type="text"
                            inputMode="decimal"
                            value={row.cells[a] ?? ''}
                            readOnly={!timelineEditing}
                            onChange={(e) => setEditCell(rowIdx, a, e.target.value)}
                          />
                        </td>
                      ))}
                      <td className="holdings-tl__total">{fmtNum(rowTotal)}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card holdings-add" aria-labelledby="holdings-add-heading">
        <h2 id="holdings-add-heading">{he.holdings.add}</h2>
        <div className="mode-row">
          <button
            type="button"
            className={manualMode === 'manual' ? '' : 'secondary'}
            onClick={() => setManualMode('manual')}
          >
            {he.holdings.manualMode}
          </button>
          <button
            type="button"
            className={manualMode === 'paste' ? '' : 'secondary'}
            onClick={() => setManualMode('paste')}
          >
            {he.holdings.pasteMode}
          </button>
        </div>

        {manualMode === 'manual' ? (
          <div className="mode active">
            <div className="holdings-toolbar">
              <label className="holdings-field">
                {he.holdings.asOf}
                <input type="date" value={mDate} onChange={(e) => setMDate(e.target.value)} />
              </label>
              <button
                type="button"
                className="secondary"
                onClick={() => setActivityRows((r) => [...r, { activity: '', balance: '' }])}
              >
                {he.holdings.addActivity}
              </button>
            </div>
            {activityRows.map((row, i) => (
              <div key={i} className="activity-row">
                <input
                  type="text"
                  list="activities-dl"
                  placeholder={he.holdings.activityPlaceholder}
                  value={row.activity}
                  onChange={(e) => {
                    const v = e.target.value;
                    setActivityRows((rows) => rows.map((x, j) => (j === i ? { ...x, activity: v } : x)));
                  }}
                />
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder={he.holdings.balancePlaceholder}
                  value={row.balance}
                  onChange={(e) => {
                    const v = e.target.value;
                    setActivityRows((rows) => rows.map((x, j) => (j === i ? { ...x, balance: v } : x)));
                  }}
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setActivityRows((rows) => rows.filter((_, j) => j !== i))}
                >
                  {he.holdings.remove}
                </button>
              </div>
            ))}
            <datalist id="activities-dl">
              {(meta?.activity_types || []).map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
            <p className="holdings-add__actions">
              <button type="button" onClick={() => void previewAndSaveRows(manualRowsPayload())}>
                {he.holdings.previewSave}
              </button>
            </p>
          </div>
        ) : (
          <div className="mode active">
            <p className="hint">{he.holdings.pasteHint}</p>
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder={'תאריך\tעובר ושב\tניירות ערך\n2026-05-01\t1200\t3300'}
            />
            <div className="holdings-toolbar holdings-add__actions">
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  const r = await fetchJson<{
                    rows?: TimelineRow[];
                    message?: string;
                    invalid_cells?: unknown[];
                  }>('/api/holdings/parse-paste-grid', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: pasteText }),
                  });
                  const data = r.data || {};
                  setParsedPasteRows(data.rows || []);
                  setPasteParseHint(
                    `${data.message || ''} Rows: ${(data.rows || []).length} Invalid cells: ${(data.invalid_cells || []).length}`,
                  );
                  setIngestStatus(JSON.stringify(data, null, 2));
                }}
              >
                {he.holdings.parse}
              </button>
              <button type="button" onClick={() => void previewAndSaveRows(parsedPasteRows)}>
                {he.holdings.previewSave}
              </button>
            </div>
            {pasteParseHint ? <div className="hint">{pasteParseHint}</div> : null}
          </div>
        )}

        {ingestStatus ? (
          <pre className="status" style={{ marginTop: '1rem' }}>
            {ingestStatus}
          </pre>
        ) : null}
      </section>
    </PageShell>
  );
}
