import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchJson } from '../lib/api';

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

function parseLooseNumber(v: unknown): number {
  const s = String(v == null ? '' : v)
    .trim()
    .replace(/,/g, '');
  if (s === '') return 0;
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

export default function Holdings() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rowsFlat, setRowsFlat] = useState<TimelineRow[]>([]);
  const [snapshotRows, setSnapshotRows] = useState<TimelineRow[]>([]);
  const [timelineEditing, setTimelineEditing] = useState(false);

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
  const [tableBump, setTableBump] = useState(0);

  const tlTableRef = useRef<HTMLTableElement | null>(null);

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
      `Latest total (${latest}): ${totalLatest.toLocaleString(undefined, { maximumFractionDigits: 2 })}` +
      (prev
        ? ` · Delta vs ${prev}: ${delta.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
        : '')
    );
  }, [pivot]);

  function hasTimelineChanges(): boolean {
    const el = tlTableRef.current;
    if (!el) return false;
    for (const inp of el.querySelectorAll<HTMLInputElement>('.tl_date_input')) {
      const orig = (inp.getAttribute('data-original-date') || '').trim();
      const cur = (inp.value || '').trim();
      if (!cur || cur !== orig) return true;
    }
    for (const inp of el.querySelectorAll<HTMLInputElement>('.tl_cell_input')) {
      const orig = parseLooseNumber(inp.getAttribute('data-original'));
      const cur = parseLooseNumber(inp.value);
      if (!Number.isFinite(cur)) return true;
      if (Math.abs(cur - orig) > 0.000001) return true;
    }
    return false;
  }

  const saveDisabled = useMemo(
    () => !timelineEditing || !hasTimelineChanges(),
    [timelineEditing, tableBump, rowsFlat],
  );

  const timelineEditedRowsPayload = (): TimelineRow[] => {
    const el = tlTableRef.current;
    if (!el) return [];
    const rows: TimelineRow[] = [];
    const seen = new Set<string>();
    for (const tr of el.querySelectorAll('tbody tr')) {
      const dateInput = tr.querySelector<HTMLInputElement>('.tl_date_input');
      const asOfDate = String(dateInput?.value || '').trim();
      if (!asOfDate) throw new Error('Date is required for all timeline rows.');
      for (const cell of tr.querySelectorAll<HTMLInputElement>('.tl_cell_input')) {
        const activityType = String(cell.getAttribute('data-activity') || '').trim();
        if (!activityType) continue;
        const raw = String(cell.value || '')
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
  };

  const timelineDateMovesPayload = (): { source_date: string; target_date: string }[] => {
    const el = tlTableRef.current;
    if (!el) return [];
    const moves: { source_date: string; target_date: string }[] = [];
    for (const tr of el.querySelectorAll('tbody tr')) {
      const fromDate = String(tr.getAttribute('data-original-date') || '').trim();
      const dateInput = tr.querySelector<HTMLInputElement>('.tl_date_input');
      const toDate = String(dateInput?.value || '').trim();
      if (!fromDate || !toDate || fromDate === toDate) continue;
      moves.push({ source_date: fromDate, target_date: toDate });
    }
    return moves;
  };

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
    <div className="app-page holdings-page">
      <h1>Holdings Control</h1>
      <p className="hint">Explore holdings over time and ingest missing data from form rows or pasted grid.</p>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Timeline</h2>
        <div className="row">
          <label className="col">
            From <input type="date" value={tlFrom} onChange={(e) => setTlFrom(e.target.value)} />
          </label>
          <label className="col">
            To <input type="date" value={tlTo} onChange={(e) => setTlTo(e.target.value)} />
          </label>
          <label className="col" style={{ minWidth: '16rem' }}>
            Activities
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
          <button type="button" onClick={() => void loadTimeline()}>
            Reload
          </button>
          <button
            type="button"
            className="secondary"
            disabled={timelineEditing}
            onClick={() => {
              setSnapshotRows([...rowsFlat]);
              setTimelineEditing(true);
            }}
          >
            Edit Table
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
              } catch (e) {
                setIngestStatus(`Error: ${e}`);
              }
            }}
          >
            Save Edits
          </button>
          <button
            type="button"
            className="secondary"
            disabled={!timelineEditing}
            onClick={() => {
              setRowsFlat([...snapshotRows]);
              setTimelineEditing(false);
            }}
          >
            Cancel
          </button>
        </div>
        <p className="hint">
          Rows: {meta?.row_count ?? 0} · Dates: {meta?.date_count ?? 0} · Range: {meta?.min_date ?? '-'} ..{' '}
          {meta?.max_date ?? '-'}
        </p>
        <div className="hint">{summaryLine}</div>
        <div
          className="holdings-scroll"
          onInput={() => setTableBump((b) => b + 1)}
          onChange={() => setTableBump((b) => b + 1)}
        >
          <table ref={tlTableRef}>
            <thead>
              <tr>
                <th>Date</th>
                {pivot.acts.map((a) => (
                  <th key={a}>{a}</th>
                ))}
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {pivot.dates.length === 0 ? (
                <tr>
                  <td colSpan={Math.max(2, pivot.acts.length + 2)}>No data</td>
                </tr>
              ) : (
                pivot.dates.map((d) => {
                  let rowTotal = 0;
                  pivot.acts.forEach((a) => {
                    rowTotal += Number((pivot.byDate[d] && pivot.byDate[d][a]) || 0);
                  });
                  return (
                    <tr key={d} data-original-date={d}>
                      <td>
                        <input
                          className="tl_date_input"
                          data-original-date={d}
                          type="date"
                          defaultValue={d}
                          readOnly={!timelineEditing}
                          onChange={() => setTableBump((b) => b + 1)}
                        />
                      </td>
                      {pivot.acts.map((a) => {
                        const v = Number((pivot.byDate[d] && pivot.byDate[d][a]) || 0);
                        const displayVal = Number.isFinite(v) ? String(v) : '0';
                        return (
                          <td key={a}>
                            <input
                              className="tl_cell_input"
                              data-activity={a}
                              data-original={displayVal}
                              type="text"
                              defaultValue={displayVal}
                              readOnly={!timelineEditing}
                              onChange={() => setTableBump((b) => b + 1)}
                            />
                          </td>
                        );
                      })}
                      <td>{rowTotal.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Manual Ingest</h2>
        <div className="mode-row">
          <button type="button" className="secondary" onClick={() => setManualMode('manual')}>
            One Date + Activities
          </button>
          <button type="button" className="secondary" onClick={() => setManualMode('paste')}>
            Paste Grid
          </button>
        </div>

        <div className={`mode ${manualMode === 'manual' ? 'active' : ''}`}>
          <div className="row">
            <label className="col">
              As of date <input type="date" value={mDate} onChange={(e) => setMDate(e.target.value)} />
            </label>
            <button
              type="button"
              className="secondary"
              onClick={() => setActivityRows((r) => [...r, { activity: '', balance: '' }])}
            >
              Add activity row
            </button>
          </div>
          {activityRows.map((row, i) => (
            <div key={i} className="activity-row">
              <input
                type="text"
                list="activities-dl"
                placeholder="activity type"
                value={row.activity}
                onChange={(e) => {
                  const v = e.target.value;
                  setActivityRows((rows) => rows.map((x, j) => (j === i ? { ...x, activity: v } : x)));
                }}
              />
              <input
                type="text"
                placeholder="balance ILS"
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
                Remove
              </button>
            </div>
          ))}
          <datalist id="activities-dl">
            {(meta?.activity_types || []).map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
          <p style={{ marginTop: '0.75rem' }}>
            <button type="button" onClick={() => void previewAndSaveRows(manualRowsPayload())}>
              Preview + Save
            </button>
          </p>
        </div>

        <div className={`mode ${manualMode === 'paste' ? 'active' : ''}`}>
          <p className="hint">Paste tab-separated grid: first column date, other columns activity types.</p>
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder={'תאריך\tעובר ושב\tניירות ערך\n2026-05-01\t1200\t3300'}
          />
          <div className="row" style={{ marginTop: '0.5rem' }}>
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
              Parse
            </button>
            <button type="button" onClick={() => void previewAndSaveRows(parsedPasteRows)}>
              Preview + Save
            </button>
          </div>
          <div className="hint">{pasteParseHint}</div>
        </div>

        <pre className="status" style={{ marginTop: '1rem' }}>
          {ingestStatus}
        </pre>
      </section>
    </div>
  );
}
