/** Shared ILS cash denomination pad + spend/receive/count register. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { deleteJson, fetchJson, patchJson, postJson } from '../lib/api';
import { he } from '../lib/he';

import '../pages/Cash.css';

export const CASH_DENOMS_AGOROT = [
  20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50, 10,
] as const;

export const CASH_PAPER_AGOROT = [20000, 10000, 5000, 2000] as const;
export const CASH_COIN_AGOROT = [1000, 500, 200, 100, 50, 10] as const;

export type CashEvent = {
  id: number;
  event_date: string;
  kind: 'spend' | 'receive' | 'count' | string;
  delta_agorot: number;
  counted_agorot: number | null;
  expected_agorot: number | null;
  denominations: Record<string, number> | null;
  note: string | null;
  category: string | null;
  created_at: string | null;
  balance_after_agorot?: number;
};

type SummaryResp = {
  ok?: boolean;
  balance_agorot?: number;
  paper_agorot?: number;
  coin_agorot?: number;
  pieces_agorot?: number;
  inventory?: Record<string, number>;
  last_count_date?: string | null;
  last_activity_month?: string | null;
  month?: string;
  denominations_agorot?: number[];
  error?: string;
  message?: string;
};

type EventsResp = {
  ok?: boolean;
  month?: string | null;
  last_activity_month?: string | null;
  balance_agorot?: number;
  events?: CashEvent[];
  error?: string;
  message?: string;
};

const ALL_MONTHS = 'all';

function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function currentMonth(): string {
  return todayIso().slice(0, 7);
}

/** Always show agora-level precision for the cash wallet. */
export function formatCashAgorot(agorot: number | null | undefined, suffix = '₪'): string {
  if (agorot == null || !Number.isFinite(agorot)) return '—';
  const shekels = agorot / 100;
  const sign = shekels < 0 ? '-' : '';
  const abs = Math.abs(shekels);
  return `${sign}${abs.toLocaleString('he-IL', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}${suffix}`;
}

function formatDenomLabel(agorot: number): string {
  if (agorot >= 100) {
    return `₪${(agorot / 100).toLocaleString('he-IL', { maximumFractionDigits: 0 })}`;
  }
  return `₪${(agorot / 100).toLocaleString('he-IL', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function emptyPad(denoms: readonly number[]): Record<number, number> {
  const out: Record<number, number> = {};
  for (const d of denoms) out[d] = 0;
  return out;
}

function padTotal(pad: Record<number, number>): number {
  let t = 0;
  for (const [k, v] of Object.entries(pad)) {
    t += Number(k) * (v || 0);
  }
  return t;
}

function padToApiMap(pad: Record<number, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(pad)) {
    if (v > 0) out[k] = v;
  }
  return out;
}

/** Signed piece deltas for spend (−) / receive (+). */
function padToDeltaMap(pad: Record<number, number>, kind: 'spend' | 'receive'): Record<string, number> {
  const sign = kind === 'spend' ? -1 : 1;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(pad)) {
    if (v > 0) out[k] = sign * v;
  }
  return out;
}

function shekelsInputFromAgorot(agorot: number): string {
  return (Math.abs(agorot) / 100).toFixed(2);
}

function parseShekelsToAgorot(raw: string): number | null {
  const n = Number(String(raw).trim().replace(',', '.'));
  if (!Number.isFinite(n) || n <= 0) return null;
  const agorot = Math.round(n * 100);
  if (agorot % 10 !== 0) return null;
  return agorot;
}

type CashRegisterProps = {
  /** Default month YYYY-MM; if omitted, uses last month with activity. */
  initialMonth?: string;
  /** Compact layout for embedding under categorize idle. */
  compact?: boolean;
};

export default function CashRegister({ initialMonth, compact = false }: CashRegisterProps) {
  const denoms = CASH_DENOMS_AGOROT;
  const [month, setMonth] = useState(initialMonth || '');
  const monthReady = Boolean(month);
  const defaultedRef = useRef(Boolean(initialMonth));

  const [balance, setBalance] = useState(0);
  const [paperAgorot, setPaperAgorot] = useState(0);
  const [coinAgorot, setCoinAgorot] = useState(0);
  const [inventory, setInventory] = useState<Record<string, number>>({});
  const [lastCount, setLastCount] = useState<string | null>(null);
  const [events, setEvents] = useState<CashEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const [mode, setMode] = useState<'txn' | 'count'>('txn');
  const [txnKind, setTxnKind] = useState<'spend' | 'receive'>('spend');
  const [eventDate, setEventDate] = useState(todayIso());
  const [note, setNote] = useState('');
  const [category, setCategory] = useState('');
  const [pad, setPad] = useState(() => emptyPad(denoms));
  const [saving, setSaving] = useState(false);

  const [backfillOpen, setBackfillOpen] = useState(false);
  const [backfillText, setBackfillText] = useState('');
  const [backfillFirstCount, setBackfillFirstCount] = useState(true);
  const [backfillBusy, setBackfillBusy] = useState(false);

  const counted = useMemo(() => padTotal(pad), [pad]);
  const padHasAny = counted > 0;

  const resolveDefaultMonth = useCallback(async () => {
    if (defaultedRef.current) return;
    try {
      const sumR = await fetchJson<SummaryResp>('/api/cash/summary');
      const activity = sumR.data.last_activity_month;
      setMonth(activity || currentMonth());
    } catch {
      setMonth(currentMonth());
    } finally {
      defaultedRef.current = true;
    }
  }, []);

  useEffect(() => {
    if (!monthReady) void resolveDefaultMonth();
  }, [monthReady, resolveDefaultMonth]);

  const load = useCallback(async () => {
    if (!month) return;
    setLoading(true);
    setError(null);
    try {
      const monthQ = encodeURIComponent(month);
      const [sumR, evR] = await Promise.all([
        fetchJson<SummaryResp>(
          month === ALL_MONTHS ? '/api/cash/summary' : `/api/cash/summary?month=${monthQ}`,
        ),
        fetchJson<EventsResp>(`/api/cash/events?month=${monthQ}`),
      ]);
      if (!sumR.ok || !sumR.data.ok) {
        setError(sumR.data.message || sumR.data.error || he.cash.error);
        setLoading(false);
        return;
      }
      if (!evR.ok || !evR.data.ok) {
        setError(evR.data.message || evR.data.error || he.cash.error);
        setLoading(false);
        return;
      }
      setBalance(sumR.data.balance_agorot ?? 0);
      setPaperAgorot(sumR.data.paper_agorot ?? 0);
      setCoinAgorot(sumR.data.coin_agorot ?? 0);
      setInventory(sumR.data.inventory ?? {});
      setLastCount(sumR.data.last_count_date ?? null);
      setEvents(evR.data.events ?? []);
      if (typeof evR.data.balance_agorot === 'number') {
        setBalance(evR.data.balance_agorot);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : he.cash.error);
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => {
    void load();
  }, [load]);

  const bump = (denom: number, delta: number) => {
    setPad((prev) => {
      const next = { ...prev };
      next[denom] = Math.max(0, (next[denom] || 0) + delta);
      return next;
    });
  };

  const setCount = (denom: number, raw: string) => {
    const n = Math.max(0, Math.floor(Number(raw) || 0));
    setPad((prev) => ({ ...prev, [denom]: n }));
  };

  const resetPad = () => setPad(emptyPad(denoms));

  const onSave = async () => {
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      let body: Record<string, unknown>;
      const cat = category.trim() || undefined;
      const noteVal = note.trim() || undefined;
      if (mode === 'count') {
        body = {
          kind: 'count',
          event_date: eventDate,
          note: noteVal,
          category: cat,
          denominations: padToApiMap(pad),
        };
      } else {
        if (counted <= 0) {
          setError(he.cash.saveFailed);
          setSaving(false);
          return;
        }
        body = {
          kind: txnKind,
          event_date: eventDate,
          note: noteVal,
          category: cat,
          amount_agorot: counted,
          denominations: padToDeltaMap(pad, txnKind),
        };
      }
      const r = await postJson<{ ok?: boolean; message?: string; error?: string; balance_agorot?: number }>(
        '/api/cash/events',
        body,
      );
      if (!r.ok || !r.data.ok) {
        setError(r.data.message || r.data.error || he.cash.saveFailed);
        setSaving(false);
        return;
      }
      resetPad();
      setNote('');
      setCategory('');
      setEventDate(todayIso());
      // Jump history to the month of the new event (unless viewing all)
      if (month !== ALL_MONTHS) {
        setMonth(eventDate.slice(0, 7));
        // load() runs via useEffect when month changes; if same month, refresh now
        if (month === eventDate.slice(0, 7)) await load();
      } else {
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : he.cash.saveFailed);
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (id: number) => {
    if (!window.confirm(he.cash.deleteConfirm)) return;
    setStatus(null);
    const r = await deleteJson<{ ok?: boolean; message?: string; error?: string }>(
      `/api/cash/events/${id}`,
    );
    if (!r.ok || !r.data.ok) {
      setError(r.data.message || r.data.error || he.cash.saveFailed);
      return;
    }
    await load();
  };

  const onPatch = async (id: number, body: Record<string, unknown>) => {
    const r = await patchJson<{ ok?: boolean; message?: string; error?: string; event?: CashEvent }>(
      `/api/cash/events/${id}`,
      body,
    );
    if (!r.ok || !r.data.ok) {
      setError(r.data.message || r.data.error || he.cash.saveFailed);
      return false;
    }
    // Date/amount changes affect running balances — reload list
    if ('event_date' in body || 'amount_agorot' in body) {
      await load();
    } else {
      setEvents((prev) =>
        prev.map((ev) => (ev.id === id ? { ...ev, ...r.data.event } : ev)),
      );
    }
    return true;
  };

  const onBackfill = async () => {
    setBackfillBusy(true);
    setError(null);
    setStatus(null);
    try {
      const r = await postJson<{
        ok?: boolean;
        inserted?: number;
        message?: string;
        error?: string;
        balance_agorot?: number;
      }>('/api/cash/backfill', {
        text: backfillText,
        first_row_is_count: backfillFirstCount,
      });
      if (!r.ok || !r.data.ok) {
        setError(r.data.message || r.data.error || he.cash.backfillFailed);
        setBackfillBusy(false);
        return;
      }
      setStatus(`${he.cash.backfillOk}: ${r.data.inserted ?? 0}`);
      setBackfillText('');
      setBackfillOpen(false);
      // Refresh default month from activity
      const sumR = await fetchJson<SummaryResp>('/api/cash/summary');
      const activity = sumR.data.last_activity_month;
      if (activity && month !== ALL_MONTHS) setMonth(activity);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : he.cash.backfillFailed);
    } finally {
      setBackfillBusy(false);
    }
  };

  const kindLabel = (k: string) => {
    if (k === 'spend') return he.cash.kindSpend;
    if (k === 'receive') return he.cash.kindReceive;
    if (k === 'count') return he.cash.kindCount;
    return k;
  };

  const hole = counted - balance;

  const renderDenomCell = (d: number) => {
    const count = pad[d] || 0;
    const active = count > 0;
    return (
      <div
        key={d}
        className={`cash-register__cell${active ? ' cash-register__cell--active' : ''}`}
      >
        <span className="cash-register__cell-face" dir="ltr">
          {formatDenomLabel(d)}
        </span>
        <div className="cash-register__cell-ctrl" dir="ltr">
          <button
            type="button"
            className="secondary cash-register__pad-btn"
            aria-label={`− ${formatDenomLabel(d)}`}
            onClick={() => bump(d, -1)}
          >
            −
          </button>
          <input
            type="number"
            min={0}
            step={1}
            className="cash-register__pad-count"
            value={count}
            aria-label={formatDenomLabel(d)}
            onChange={(e) => setCount(d, e.target.value)}
            onFocus={(e) => e.target.select()}
          />
          <button
            type="button"
            className="secondary cash-register__pad-btn"
            aria-label={`+ ${formatDenomLabel(d)}`}
            onClick={() => bump(d, 1)}
          >
            +
          </button>
        </div>
        {active ? (
          <span className="cash-register__cell-sub" dir="ltr">
            {formatCashAgorot(d * count)}
          </span>
        ) : (
          <span className="cash-register__cell-sub cash-register__cell-sub--empty"> </span>
        )}
      </div>
    );
  };

  return (
    <div className={`cash-register${compact ? ' cash-register--compact' : ''}`}>
      <div className="cash-register__kpi">
        <div className="cash-register__kpi-main">
          <div className="cash-register__kpi-top">
            <span className="cash-register__kpi-label">{he.cash.balance}</span>
            <div className="cash-register__kpi-side cash-register__kpi-side--inline">
              <span className="hint">
                {he.cash.lastCount}: {lastCount || he.cash.neverCounted}
              </span>
              {!compact ? (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setBackfillOpen((v) => !v)}
                >
                  {he.cash.backfill}
                </button>
              ) : null}
            </div>
          </div>
          <div className="cash-register__kpi-split">
            <div className="cash-register__kpi-part">
              <span className="hint">{he.cash.paperCash}</span>
              <strong className="cash-register__kpi-value cash-register__kpi-value--sm" dir="ltr">
                {formatCashAgorot(paperAgorot)}
              </strong>
            </div>
            <div className="cash-register__kpi-part">
              <span className="hint">{he.cash.coinCash}</span>
              <strong className="cash-register__kpi-value cash-register__kpi-value--sm" dir="ltr">
                {formatCashAgorot(coinAgorot)}
              </strong>
            </div>
            <div className="cash-register__kpi-part cash-register__kpi-part--total">
              <span className="hint">{he.cash.totalCash}</span>
              <strong className="cash-register__kpi-value" dir="ltr">
                {formatCashAgorot(paperAgorot + coinAgorot)}
              </strong>
            </div>
          </div>
          <div className="cash-register__inv">
            <div className="cash-register__inv-row">
              <span className="cash-register__inv-label">{he.cash.paperCash}</span>
              <div className="cash-register__inv-grid">
                {CASH_PAPER_AGOROT.map((d) => {
                  const n = inventory[String(d)] ?? 0;
                  return (
                    <div
                      key={d}
                      className={`cash-register__inv-chip${n > 0 ? ' cash-register__inv-chip--on' : ''}`}
                      title={`${formatDenomLabel(d)} × ${n}`}
                    >
                      <span className="cash-register__inv-face" dir="ltr">
                        {formatDenomLabel(d)}
                      </span>
                      <strong className="cash-register__inv-count" dir="ltr">
                        ×{n}
                      </strong>
                      <span className="cash-register__inv-sum" dir="ltr">
                        {formatCashAgorot(d * n)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="cash-register__inv-row">
              <span className="cash-register__inv-label">{he.cash.coinCash}</span>
              <div className="cash-register__inv-grid">
                {CASH_COIN_AGOROT.map((d) => {
                  const n = inventory[String(d)] ?? 0;
                  return (
                    <div
                      key={d}
                      className={`cash-register__inv-chip${n > 0 ? ' cash-register__inv-chip--on' : ''}`}
                      title={`${formatDenomLabel(d)} × ${n}`}
                    >
                      <span className="cash-register__inv-face" dir="ltr">
                        {formatDenomLabel(d)}
                      </span>
                      <strong className="cash-register__inv-count" dir="ltr">
                        ×{n}
                      </strong>
                      <span className="cash-register__inv-sum" dir="ltr">
                        {formatCashAgorot(d * n)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>

      {error ? (
        <p className="banner banner--err" role="alert">
          {error}
        </p>
      ) : null}
      {status ? <p className="hint">{status}</p> : null}

      {backfillOpen && !compact ? (
        <div className="cash-register__backfill card">
          <p className="hint">{he.cash.backfillHint}</p>
          <textarea
            dir="ltr"
            className="cash-register__backfill-text"
            rows={8}
            value={backfillText}
            placeholder={he.cash.backfillPlaceholder}
            onChange={(e) => setBackfillText(e.target.value)}
          />
          <label className="cash-register__backfill-flag">
            <input
              type="checkbox"
              checked={backfillFirstCount}
              onChange={(e) => setBackfillFirstCount(e.target.checked)}
            />
            <span>{he.cash.backfillFirstCount}</span>
          </label>
          <button
            type="button"
            disabled={backfillBusy || !backfillText.trim()}
            onClick={() => void onBackfill()}
          >
            {backfillBusy ? he.cash.saving : he.cash.backfillRun}
          </button>
        </div>
      ) : null}

      <div className="cash-register__composer card">
        <div className="cash-register__toolbar">
          <div className="cash-register__modes" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'txn'}
              className={`secondary${mode === 'txn' ? ' active' : ''}`}
              onClick={() => setMode('txn')}
            >
              {he.cash.modeTxn}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'count'}
              className={`secondary${mode === 'count' ? ' active' : ''}`}
              onClick={() => setMode('count')}
            >
              {he.cash.modeCount}
            </button>
          </div>
          {mode === 'txn' ? (
            <div className="cash-register__kind-toggle" role="group">
              <button
                type="button"
                className={`secondary${txnKind === 'spend' ? ' active cash-register__kind--spend' : ''}`}
                onClick={() => setTxnKind('spend')}
              >
                {he.cash.spend}
              </button>
              <button
                type="button"
                className={`secondary${txnKind === 'receive' ? ' active cash-register__kind--receive' : ''}`}
                onClick={() => setTxnKind('receive')}
              >
                {he.cash.receive}
              </button>
            </div>
          ) : null}
        </div>

        <div className="cash-register__meta-row">
          <label>
            <span>{he.cash.date}</span>
            <input
              type="date"
              dir="ltr"
              value={eventDate}
              onChange={(e) => setEventDate(e.target.value)}
            />
          </label>
          <label className="cash-register__note">
            <span>{he.cash.category}</span>
            <input
              type="text"
              value={category}
              placeholder={he.cash.categoryPlaceholder}
              onChange={(e) => setCategory(e.target.value)}
            />
          </label>
          <label className="cash-register__note">
            <span>{he.cash.note}</span>
            <input
              type="text"
              value={note}
              placeholder={he.cash.notePlaceholder}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        </div>

        <p className="hint cash-register__pad-hint">{he.cash.padHint}</p>

        <div className="cash-register__pad-block">
          <div className="cash-register__pad-group">
            <div className="cash-register__pad-group-label">{he.cash.paperCash}</div>
            <div className="cash-register__pad-grid cash-register__pad-grid--paper">
              {CASH_PAPER_AGOROT.map(renderDenomCell)}
            </div>
          </div>
          <div className="cash-register__pad-group">
            <div className="cash-register__pad-group-label">{he.cash.coinCash}</div>
            <div className="cash-register__pad-grid cash-register__pad-grid--coins">
              {CASH_COIN_AGOROT.map(renderDenomCell)}
            </div>
          </div>
        </div>

        <div className="cash-register__footer">
          <div className="cash-register__totals">
            {mode === 'count' ? (
              <>
                <div>
                  <span className="hint">{he.cash.expected}</span>
                  <strong dir="ltr">{formatCashAgorot(balance)}</strong>
                </div>
                <div>
                  <span className="hint">{he.cash.counted}</span>
                  <strong dir="ltr">{formatCashAgorot(counted)}</strong>
                </div>
                <div className={hole !== 0 ? 'cash-register__hole' : ''}>
                  <span className="hint">
                    {he.cash.hole}
                    {hole !== 0 ? ` · ${he.cash.unexplained}` : ''}
                  </span>
                  <strong dir="ltr">{formatCashAgorot(hole)}</strong>
                </div>
              </>
            ) : (
              <div>
                <span className="hint">{he.cash.total}</span>
                <strong className="cash-register__footer-total" dir="ltr">
                  {formatCashAgorot(counted)}
                </strong>
              </div>
            )}
          </div>
          <div className="cash-register__actions">
            {padHasAny ? (
              <button type="button" className="secondary" onClick={resetPad}>
                {he.cash.clearPad}
              </button>
            ) : null}
            <button
              type="button"
              disabled={saving || (mode === 'txn' && counted <= 0)}
              onClick={() => void onSave()}
            >
              {saving ? he.cash.saving : he.cash.save}
            </button>
          </div>
        </div>
      </div>

      <section className="cash-register__history" aria-labelledby="cash-history-heading">
        <div className="cash-register__history-head">
          <h2 id="cash-history-heading">{he.cash.history}</h2>
          <div className="cash-register__history-filters">
            <label className="cash-register__month">
              <span>{he.cash.month}</span>
              <input
                type="month"
                dir="ltr"
                value={month === ALL_MONTHS ? '' : month}
                disabled={!monthReady || month === ALL_MONTHS}
                onChange={(e) => {
                  if (e.target.value) setMonth(e.target.value);
                }}
              />
            </label>
            <button
              type="button"
              className={`secondary${month === ALL_MONTHS ? ' active' : ''}`}
              onClick={() => setMonth(ALL_MONTHS)}
            >
              {he.cash.allMonths}
            </button>
          </div>
        </div>
        <p className="hint cash-register__history-hint">{he.cash.historyEditHint}</p>
        {!monthReady || loading ? (
          <p className="hint">{he.cash.loading}</p>
        ) : events.length === 0 ? (
          <p className="hint">{he.cash.empty}</p>
        ) : (
          <div className="cash-register__table-wrap">
            <table className="cash-register__table">
              <thead>
                <tr>
                  <th>{he.cash.date}</th>
                  <th>{he.cash.modeTxn}</th>
                  <th>{he.cash.category}</th>
                  <th>{he.cash.note}</th>
                  <th dir="ltr">{he.cash.amountShekels}</th>
                  <th dir="ltr">{he.cash.afterBalance}</th>
                  <th aria-label={he.cash.delete} />
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => {
                  const isHole = ev.kind === 'count' && ev.delta_agorot !== 0;
                  const canEditAmount =
                    (ev.kind === 'spend' || ev.kind === 'receive') && !ev.denominations;
                  const rowKind =
                    ev.kind === 'spend'
                      ? 'cash-register__row--spend'
                      : ev.kind === 'receive'
                        ? 'cash-register__row--receive'
                        : isHole
                          ? 'cash-register__row--hole'
                          : 'cash-register__row--count';
                  return (
                    <tr key={ev.id} className={rowKind}>
                      <td>
                        <input
                          type="date"
                          dir="ltr"
                          className="cash-register__cat-input"
                          defaultValue={ev.event_date}
                          key={`date-${ev.id}-${ev.event_date}`}
                          onBlur={(e) => {
                            const next = e.target.value;
                            if (next && next !== ev.event_date) {
                              void onPatch(ev.id, { event_date: next });
                            }
                          }}
                        />
                      </td>
                      <td>
                        <span className={`cash-register__kind-pill cash-register__kind-pill--${ev.kind}`}>
                          {kindLabel(ev.kind)}
                        </span>
                      </td>
                      <td>
                        <input
                          type="text"
                          className="cash-register__cat-input"
                          defaultValue={ev.category || ''}
                          key={`cat-${ev.id}-${ev.category || ''}`}
                          placeholder={he.cash.categoryPlaceholder}
                          onBlur={(e) => {
                            const next = e.target.value;
                            if ((ev.category || '') !== next.trim()) {
                              void onPatch(ev.id, { category: next.trim() || null });
                            }
                          }}
                        />
                      </td>
                      <td>
                        <input
                          type="text"
                          className="cash-register__cat-input"
                          defaultValue={ev.note || ''}
                          key={`note-${ev.id}-${ev.note || ''}`}
                          placeholder={he.cash.notePlaceholder}
                          onBlur={(e) => {
                            const next = e.target.value;
                            if ((ev.note || '') !== next.trim()) {
                              void onPatch(ev.id, { note: next.trim() || null });
                            }
                          }}
                        />
                        {isHole ? (
                          <span className="cash-register__hole-tag"> · {he.cash.unexplained}</span>
                        ) : null}
                      </td>
                      <td dir="ltr" className="cash-register__amt-cell">
                        {canEditAmount ? (
                          <input
                            type="text"
                            inputMode="decimal"
                            className="cash-register__cat-input cash-register__amt-input"
                            defaultValue={shekelsInputFromAgorot(ev.delta_agorot)}
                            key={`amt-${ev.id}-${ev.delta_agorot}`}
                            onBlur={(e) => {
                              const agorot = parseShekelsToAgorot(e.target.value);
                              if (agorot == null) {
                                e.target.value = shekelsInputFromAgorot(ev.delta_agorot);
                                return;
                              }
                              if (agorot !== Math.abs(ev.delta_agorot)) {
                                void onPatch(ev.id, { amount_agorot: agorot });
                              }
                            }}
                          />
                        ) : (
                          formatCashAgorot(ev.delta_agorot)
                        )}
                      </td>
                      <td dir="ltr">
                        {ev.balance_after_agorot != null
                          ? formatCashAgorot(ev.balance_after_agorot)
                          : '—'}
                      </td>
                      <td>
                        <button type="button" className="secondary cash-register__del" onClick={() => void onDelete(ev.id)}>
                          {he.cash.delete}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
