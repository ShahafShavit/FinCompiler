import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import PageShell from '../components/PageShell';
import { fetchJson, formatMoney, patchJson, isAbortError } from '../lib/api';
import { hashColor } from '../lib/chartColors';
import { he } from '../lib/he';
import { dropUndoEntry } from '../lib/undoStack';

import './Installments.css';

const REC_SELECT_KEY = 'fincompiler.installments.recSelected';

function loadRecSelection(): Set<string> {
  try {
    const raw = localStorage.getItem(REC_SELECT_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((x): x is string => typeof x === 'string'));
  } catch {
    return new Set();
  }
}

function saveRecSelection(ids: Set<string>) {
  try {
    localStorage.setItem(REC_SELECT_KEY, JSON.stringify([...ids]));
  } catch {
    /* ignore quota / private mode */
  }
}

type TimelineEntry = {
  month: string;
  x: number;
  amount: number | null;
  state: 'observed' | 'expected' | 'missing' | 'before_cutoff' | string;
};

type MergedSource = {
  plan_id: string;
  merchant: string;
  category?: string;
  merged_into?: string;
  monthly_amount?: number;
  amount?: number;
  amount_mode?: string;
  status?: string;
};

type InstallmentPlan = {
  plan_id: string;
  merchant: string;
  category?: string;
  amount: number;
  paid_count: number;
  total_count: number;
  remaining_count: number;
  remaining_ils: number;
  next_month: string | null;
  last_month: string | null;
  last4s?: string[];
  status: string;
  hidden?: boolean;
  settled?: boolean;
  due_this_month?: number;
  timeline?: TimelineEntry[];
  tx_ids?: number[];
  merged_sources?: MergedSource[];
};

type RecurringFee = {
  plan_id: string;
  merchant: string;
  category?: string;
  extra_label?: string;
  amount_mode?: 'fixed' | 'variable' | string;
  monthly_amount: number;
  last_month_amount?: number;
  months_seen: number;
  first_seen?: string;
  last_seen?: string;
  next_expected: string | null;
  lifetime_paid: number;
  last4s?: string[];
  status: string;
  hidden?: boolean;
  tx_ids?: number[];
  merged_sources?: MergedSource[];
};

type UndoEntry = {
  planId: string;
  body: Record<string, unknown>;
  label: string;
};

type PlansResponse = {
  ok?: boolean;
  as_of?: string;
  ledger_exists?: boolean;
  error?: string;
  message?: string;
  installments?: {
    summary?: {
      active?: number;
      due_this_month?: number;
      remaining_total?: number;
      nearest_last_payment?: string | null;
    };
    plans?: InstallmentPlan[];
  };
  recurring?: {
    summary?: {
      active?: number;
      monthly_burn?: number;
      lapsed?: number;
      variable_active?: number;
      top_active?: {
        plan_id: string;
        merchant: string;
        monthly_amount: number;
        amount_mode?: string;
        category?: string;
      }[];
    };
    fees?: RecurringFee[];
  };
};

type InstFilter = 'all' | 'active' | 'completed' | 'gapped';
type RecFilter = 'all' | 'active' | 'lapsed';

function statusLabelInst(s: string): string {
  switch (s) {
    case 'active':
      return he.installments.statusActive;
    case 'completed':
      return he.installments.statusCompleted;
    case 'paid_off':
      return he.installments.statusPaidOff;
    case 'gapped':
      return he.installments.statusGapped;
    default:
      return s;
  }
}

function statusLabelRec(s: string): string {
  switch (s) {
    case 'active':
      return he.installments.recStatusActive;
    case 'lapsed':
      return he.installments.recStatusLapsed;
    default:
      return s;
  }
}

function timelineStateLabel(s: string): string {
  switch (s) {
    case 'observed':
      return he.installments.stateObserved;
    case 'expected':
      return he.installments.stateExpected;
    case 'missing':
      return he.installments.stateMissing;
    case 'before_cutoff':
      return he.installments.stateBeforeCutoff;
    case 'settled':
      return he.installments.stateSettled;
    default:
      return s;
  }
}

function exploreHref(merchant: string, txIds?: number[]): string {
  if (txIds && txIds.length > 0) {
    return `/explore?ids=${txIds.join(',')}`;
  }
  return `/explore?q=${encodeURIComponent(merchant)}`;
}

export default function Installments() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get('q') || '';
  const includeHidden = sp.get('include_hidden') === '1';
  const instFilter = (sp.get('inst') as InstFilter) || 'all';
  const recFilter = (sp.get('rec') as RecFilter) || 'all';

  const [data, setData] = useState<PlansResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [qDraft, setQDraft] = useState(q);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [mergeFor, setMergeFor] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedRec, setSelectedRec] = useState<Set<string>>(loadRecSelection);
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [mergedOpen, setMergedOpen] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setQDraft(q);
  }, [q]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      const next = new URLSearchParams(sp);
      const trimmed = qDraft.trim();
      if (trimmed) next.set('q', trimmed);
      else next.delete('q');
      if (next.toString() !== sp.toString()) setSp(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce qDraft only
  }, [qDraft]);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setErr(null);
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q.trim());
    if (includeHidden) params.set('include_hidden', '1');
    try {
      const r = await fetchJson<PlansResponse>(`/api/installment-plans?${params}`, {
        cache: 'no-store',
        signal,
      });
      if (signal?.aborted) return;
      if (!r.ok || r.data.ok === false) {
        setErr(r.data.message || r.data.error || he.installments.error);
        setData(null);
      } else {
        setData(r.data);
      }
    } catch (e) {
      if (isAbortError(e) || signal?.aborted) return;
      setErr(he.installments.error);
      setData(null);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [q, includeHidden]);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  const undoLabelFor = (body: Record<string, unknown>): string | null => {
    if ('merged_into' in body) {
      if (body.merged_into == null || body.merged_into === '') {
        return he.installments.undoMerge; // will re-apply merge on undo of unmerge — label is fine as "ביטול מיזוג" inverse
      }
      return he.installments.undoMerge;
    }
    if ('hidden' in body) {
      return body.hidden ? he.installments.undoHide : he.installments.undoUnhide;
    }
    if ('settled' in body) {
      return body.settled ? he.installments.undoSettle : he.installments.undoUnsettle;
    }
    if ('split_by' in body) {
      return body.split_by == null || body.split_by === ''
        ? he.installments.undoClearSplit
        : he.installments.undoSplit;
    }
    return null;
  };

  const patchPlan = async (
    planId: string,
    body: Record<string, unknown>,
    opts?: {
      skipUndo?: boolean;
      priorSplitBy?: string | null;
      /** When clearing a merge, restore target on undo. */
      priorMergedInto?: string | null;
    },
  ) => {
    setBusyId(planId);
    try {
      const enc = encodeURIComponent(planId);
      const r = await patchJson<PlansResponse>(`/api/installment-plans/${enc}`, body);
      if (!r.ok || r.data.ok === false) {
        setErr(r.data.message || r.data.error || he.installments.error);
      } else {
        if (!opts?.skipUndo) {
          let undoBody: Record<string, unknown> | null = null;
          let label: string | null = null;
          if ('merged_into' in body) {
            if (body.merged_into != null && body.merged_into !== '') {
              undoBody = { merged_into: null };
              label = he.installments.undoMerge;
            } else if (opts?.priorMergedInto) {
              undoBody = { merged_into: opts.priorMergedInto };
              label = he.installments.redoMerge;
            }
          } else if ('hidden' in body) {
            undoBody = { hidden: !body.hidden };
            label = undoLabelFor(body);
          } else if ('settled' in body) {
            undoBody = { settled: !body.settled };
            label = undoLabelFor(body);
          } else if ('split_by' in body) {
            if (body.split_by == null || body.split_by === '') {
              if (opts?.priorSplitBy) undoBody = { split_by: opts.priorSplitBy };
            } else {
              undoBody = { split_by: null };
            }
            label = undoBody ? undoLabelFor(body) : null;
          }
          if (undoBody && label) {
            setUndoStack((prev) => [...prev, { planId, body: undoBody, label }].slice(-20));
          }
        }
        await load();
        setMergeFor(null);
        return true;
      }
      return false;
    } catch {
      setErr(he.installments.error);
      return false;
    } finally {
      setBusyId(null);
    }
  };

  const undoLast = async () => {
    const last = undoStack[undoStack.length - 1];
    if (!last || busyId) return;
    const ok = await patchPlan(last.planId, last.body, { skipUndo: true });
    if (ok) {
      setUndoStack((prev) => dropUndoEntry(prev, last));
    }
  };

  useEffect(() => {
    saveRecSelection(selectedRec);
  }, [selectedRec]);

  const plans = data?.installments?.plans || [];
  const fees = data?.recurring?.fees || [];
  const instSummary = data?.installments?.summary;
  const recSummary = data?.recurring?.summary;

  useEffect(() => {
    if (fees.length === 0) return;
    const known = new Set(fees.map((f) => f.plan_id));
    setSelectedRec((prev) => {
      if (prev.size === 0) return prev;
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (known.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [fees]);

  const filteredPlans = useMemo(() => {
    return plans.filter((p) => {
      if (instFilter === 'all') return true;
      if (instFilter === 'active') return p.status === 'active' || p.status === 'gapped';
      if (instFilter === 'completed') return p.status === 'completed' || p.status === 'paid_off';
      if (instFilter === 'gapped') return p.status === 'gapped';
      return true;
    });
  }, [plans, instFilter]);

  const filteredFees = useMemo(() => {
    return fees.filter((p) => {
      if (recFilter === 'all') return true;
      if (recFilter === 'active') return p.status === 'active';
      if (recFilter === 'lapsed') return p.status === 'lapsed';
      return true;
    });
  }, [fees, recFilter]);

  const activeFees = useMemo(
    () => fees.filter((f) => f.status === 'active' && !f.hidden),
    [fees],
  );

  const selectedSum = useMemo(() => {
    if (selectedRec.size === 0) return null;
    let sum = 0;
    for (const f of fees) {
      if (selectedRec.has(f.plan_id)) sum += f.monthly_amount || 0;
    }
    return Math.round(sum * 100) / 100;
  }, [fees, selectedRec]);

  const chartRows = useMemo(() => {
    const pool =
      selectedRec.size > 0
        ? fees.filter((f) => selectedRec.has(f.plan_id))
        : activeFees;
    const sorted = [...pool].sort(
      (a, b) => (b.monthly_amount || 0) - (a.monthly_amount || 0),
    );
    const topN = 8;
    const top = sorted.slice(0, topN);
    const rest = sorted.slice(topN);
    const rows = top.map((f) => ({
      name: f.merchant || f.plan_id,
      amount: f.monthly_amount || 0,
      plan_id: f.plan_id,
    }));
    if (rest.length > 0) {
      rows.push({
        name: he.installments.recChartOther,
        amount: Math.round(rest.reduce((s, f) => s + (f.monthly_amount || 0), 0) * 100) / 100,
        plan_id: '__other__',
      });
    }
    return rows.filter((r) => r.amount > 0);
  }, [activeFees, fees, selectedRec]);

  const toggleRecSelect = (planId: string) => {
    setSelectedRec((prev) => {
      const next = new Set(prev);
      if (next.has(planId)) next.delete(planId);
      else next.add(planId);
      return next;
    });
  };

  const selectAllActive = () => {
    setSelectedRec(new Set(activeFees.map((f) => f.plan_id)));
  };

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(sp);
    if (value == null || value === '' || value === 'all' || (key === 'include_hidden' && value === '0')) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setSp(next, { replace: true });
  };

  const toolbar = (
    <div className="inst-toolbar">
      <label className="inst-field inst-field--grow">
        <span>{he.installments.search}</span>
        <input
          type="search"
          value={qDraft}
          placeholder={he.installments.searchPlaceholder}
          onChange={(e) => setQDraft(e.target.value)}
        />
      </label>
      <label className="inst-check">
        <input
          type="checkbox"
          checked={includeHidden}
          onChange={(e) => setParam('include_hidden', e.target.checked ? '1' : '0')}
        />
        <span>{he.installments.showHidden}</span>
      </label>
      {undoStack.length > 0 ? (
        <button
          type="button"
          className="secondary inst-undo-btn"
          disabled={Boolean(busyId)}
          onClick={() => void undoLast()}
          title={undoStack[undoStack.length - 1]?.label}
        >
          {he.installments.undoLast}
          {undoStack.length > 1 ? ` (${undoStack.length})` : ''}
        </button>
      ) : null}
    </div>
  );

  const renderMergedSources = (planId: string, sources: MergedSource[] | undefined) => {
    if (!sources || sources.length === 0) return null;
    const open = mergedOpen.has(planId);
    return (
      <div className="inst-merged">
        <button
          type="button"
          className="inst-merged__toggle"
          aria-expanded={open}
          onClick={() =>
            setMergedOpen((prev) => {
              const next = new Set(prev);
              if (next.has(planId)) next.delete(planId);
              else next.add(planId);
              return next;
            })
          }
        >
          <span>
            {he.installments.mergedSources} ({sources.length})
          </span>
          <span className="inst-merged__chevron" aria-hidden>
            {open ? '▾' : '▸'}
          </span>
        </button>
        {open ? (
          <ul className="inst-merged__list">
            {sources.map((s) => (
              <li key={s.plan_id}>
                <span className="inst-merged__name">
                  {s.merchant || s.plan_id}
                  {s.category ? ` · ${s.category}` : ''}
                </span>
                <button
                  type="button"
                  className="secondary"
                  disabled={busyId === s.plan_id}
                  onClick={() =>
                    void patchPlan(
                      s.plan_id,
                      { merged_into: null },
                      { priorMergedInto: s.merged_into || undefined },
                    )
                  }
                >
                  {he.installments.unmerge}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };

  const renderActions = (
    planId: string,
    hidden: boolean | undefined,
    peers: { plan_id: string; merchant: string }[],
    opts?: { settled?: boolean; showSettle?: boolean; mergedSources?: MergedSource[] },
  ) => {
    const busy = busyId === planId;
    const settled = Boolean(opts?.settled);
    return (
      <div className="inst-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void patchPlan(planId, { hidden: !hidden })}
        >
          {hidden ? he.installments.unhide : he.installments.hide}
        </button>
        {opts?.showSettle ? (
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void patchPlan(planId, { settled: !settled })}
          >
            {settled ? he.installments.unsettleRest : he.installments.settleRest}
          </button>
        ) : null}
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => setMergeFor(mergeFor === planId ? null : planId)}
        >
          {he.installments.merge}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void patchPlan(planId, { split_by: 'last4' })}
        >
          {he.installments.splitLast4}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void patchPlan(planId, { split_by: 'amount' })}
        >
          {he.installments.splitAmount}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void patchPlan(planId, { split_by: null })}
        >
          {he.installments.clearSplit}
        </button>
        {mergeFor === planId ? (
          <label className="inst-merge-pick">
            <span>{he.installments.mergePick}</span>
            <select
              defaultValue=""
              disabled={busy}
              onChange={(e) => {
                const v = e.target.value;
                if (v) void patchPlan(planId, { merged_into: v });
              }}
            >
              <option value="">{he.installments.mergeCancel}</option>
              {peers
                .filter((p) => p.plan_id !== planId)
                .map((p) => (
                  <option key={p.plan_id} value={p.plan_id}>
                    {p.merchant || p.plan_id}
                  </option>
                ))}
            </select>
          </label>
        ) : null}
        {renderMergedSources(planId, opts?.mergedSources)}
      </div>
    );
  };

  return (
    <PageShell
      title={he.installments.title}
      sub={he.installments.sub}
      width="full"
      className="inst-page"
      actions={toolbar}
    >
      {data?.as_of ? (
        <p className="inst-asof" dir="ltr">
          {he.installments.asOf}
          {data.as_of}
        </p>
      ) : null}

      {err ? <p className="inst-msg inst-msg--err">{err}</p> : null}
      {loading && !data ? <p className="inst-msg">{he.installments.loading}</p> : null}

      {!loading || data ? (
        <>
          <section className="inst-section">
            <header className="inst-section__head">
              <div>
                <h2>{he.installments.sectionTitle}</h2>
                <p className="inst-section__sub">{he.installments.sectionSub}</p>
              </div>
              <div className="inst-seg">
                {(
                  [
                    ['all', he.installments.filterAll],
                    ['active', he.installments.filterActive],
                    ['gapped', he.installments.filterGapped],
                    ['completed', he.installments.filterCompleted],
                  ] as const
                ).map(([k, label]) => (
                  <button
                    key={k}
                    type="button"
                    className={instFilter === k ? 'active' : undefined}
                    onClick={() => setParam('inst', k)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </header>

            <div className="inst-kpi-strip">
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.kpiActive}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {instSummary?.active ?? 0}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.kpiDue}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {formatMoney(instSummary?.due_this_month ?? 0)}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.kpiRemaining}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {formatMoney(instSummary?.remaining_total ?? 0)}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.kpiLastPayment}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {instSummary?.nearest_last_payment || '—'}
                </div>
              </div>
            </div>

            {filteredPlans.length === 0 ? (
              <p className="inst-msg">{he.installments.empty}</p>
            ) : (
              <div className="inst-table-wrap">
                <table className="inst-table">
                  <thead>
                    <tr>
                      <th>{he.installments.colMerchant}</th>
                      <th dir="ltr">{he.installments.colProgress}</th>
                      <th dir="ltr">{he.installments.colRemaining}</th>
                      <th dir="ltr">{he.installments.colNext}</th>
                      <th dir="ltr">{he.installments.colLast}</th>
                      <th>{he.installments.colStatus}</th>
                      <th>{he.installments.colActions}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPlans.map((p) => {
                      const open = expanded === p.plan_id;
                      return (
                        <Fragment key={p.plan_id}>
                          <tr
                            className={
                              p.hidden
                                ? 'inst-row--hidden'
                                : p.status === 'gapped'
                                  ? 'inst-row--gapped'
                                  : undefined
                            }
                          >
                            <td>
                              <button
                                type="button"
                                className="inst-merchant-btn"
                                onClick={() => setExpanded(open ? null : p.plan_id)}
                              >
                                {p.merchant || '—'}
                              </button>
                              {p.category ? (
                                <span className="inst-cat">{p.category}</span>
                              ) : null}
                              <Link
                                className="inst-explore"
                                to={exploreHref(p.merchant, p.tx_ids)}
                              >
                                {he.installments.explore}
                              </Link>
                            </td>
                            <td dir="ltr">
                              {p.paid_count}/{p.total_count}
                            </td>
                            <td dir="ltr">{formatMoney(p.remaining_ils)}</td>
                            <td dir="ltr">{p.next_month || '—'}</td>
                            <td dir="ltr">{p.last_month || '—'}</td>
                            <td>
                              <span className={`inst-badge inst-badge--${p.status}`}>
                                {statusLabelInst(p.status)}
                              </span>
                            </td>
                            <td>
                              {renderActions(
                                p.plan_id,
                                p.hidden,
                                plans.map((x) => ({
                                  plan_id: x.plan_id,
                                  merchant: x.merchant,
                                })),
                                {
                                  settled: p.settled,
                                  showSettle: true,
                                  mergedSources: p.merged_sources,
                                },
                              )}
                            </td>
                          </tr>
                          {open && p.timeline ? (
                            <tr className="inst-timeline-row">
                              <td colSpan={7}>
                                <table className="inst-timeline">
                                  <thead>
                                    <tr>
                                      <th>{he.installments.timelineMonth}</th>
                                      <th>{he.installments.timelineX}</th>
                                      <th>{he.installments.timelineAmount}</th>
                                      <th>{he.installments.timelineState}</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {p.timeline.map((t) => (
                                      <tr key={`${p.plan_id}-${t.x}`}>
                                        <td dir="ltr">{t.month}</td>
                                        <td dir="ltr">{t.x}</td>
                                        <td dir="ltr">{formatMoney(t.amount)}</td>
                                        <td>{timelineStateLabel(t.state)}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
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

          <section className="inst-section">
            <header className="inst-section__head">
              <div>
                <h2>{he.installments.recurringTitle}</h2>
                <p className="inst-section__sub">{he.installments.recurringSub}</p>
              </div>
              <div className="inst-seg">
                {(
                  [
                    ['all', he.installments.filterAll],
                    ['active', he.installments.recFilterActive],
                    ['lapsed', he.installments.recFilterLapsed],
                  ] as const
                ).map(([k, label]) => (
                  <button
                    key={k}
                    type="button"
                    className={recFilter === k ? 'active' : undefined}
                    onClick={() => setParam('rec', k)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </header>

            <div className="inst-kpi-strip">
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.recKpiActive}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {recSummary?.active ?? 0}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.recKpiBurn}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {formatMoney(recSummary?.monthly_burn ?? 0)}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.recKpiVariable}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {recSummary?.variable_active ?? 0}
                </div>
              </div>
              <div className="inst-kpi">
                <div className="inst-kpi__label">{he.installments.recKpiLapsed}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {recSummary?.lapsed ?? 0}
                </div>
              </div>
              {selectedSum != null ? (
                <div className="inst-kpi inst-kpi--selected">
                  <div className="inst-kpi__label">
                    {he.installments.recKpiSelected} ({selectedRec.size})
                  </div>
                  <div className="inst-kpi__value" dir="ltr">
                    {formatMoney(selectedSum)}
                  </div>
                </div>
              ) : null}
            </div>

            {chartRows.length > 0 ? (
              <div className="inst-rec-chart">
                <div className="inst-rec-chart__head">
                  <h3>{he.installments.recChartTitle}</h3>
                  <div className="inst-rec-chart__actions">
                    <button type="button" className="secondary" onClick={selectAllActive}>
                      {he.installments.recSelectAll}
                    </button>
                    {selectedRec.size > 0 ? (
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => setSelectedRec(new Set())}
                      >
                        {he.installments.recClearSelect}
                      </button>
                    ) : null}
                  </div>
                </div>
                <div className="inst-rec-chart__body" dir="ltr">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={chartRows}
                        dataKey="amount"
                        nameKey="name"
                        cx="42%"
                        cy="50%"
                        innerRadius={48}
                        outerRadius={78}
                        paddingAngle={1}
                      >
                        {chartRows.map((row) => (
                          <Cell key={row.plan_id} fill={hashColor(row.name)} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(value) => formatMoney(Number(value) || 0)}
                        labelFormatter={(label) => String(label)}
                      />
                      <Legend
                        layout="vertical"
                        align="right"
                        verticalAlign="middle"
                        wrapperStyle={{ fontSize: '0.72rem', maxWidth: '46%' }}
                        formatter={(value) =>
                          String(value).length > 22 ? `${String(value).slice(0, 20)}…` : String(value)
                        }
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <p className="inst-rec-chart__hint">{he.installments.recChartHint}</p>
              </div>
            ) : null}

            {filteredFees.length === 0 ? (
              <p className="inst-msg">{he.installments.empty}</p>
            ) : (
              <div className="inst-table-wrap">
                <table className="inst-table">
                  <thead>
                    <tr>
                      <th className="inst-col-check">{he.installments.recSelect}</th>
                      <th>{he.installments.recColMerchant}</th>
                      <th dir="ltr">{he.installments.recColAmount}</th>
                      <th dir="ltr">{he.installments.recColMonths}</th>
                      <th dir="ltr">{he.installments.recColNext}</th>
                      <th dir="ltr">{he.installments.recColLifetime}</th>
                      <th>{he.installments.recColStatus}</th>
                      <th>{he.installments.colActions}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredFees.map((f) => (
                      <tr
                        key={f.plan_id}
                        className={
                          f.hidden
                            ? 'inst-row--hidden'
                            : f.status === 'lapsed'
                              ? 'inst-row--lapsed'
                              : selectedRec.has(f.plan_id)
                                ? 'inst-row--selected'
                                : undefined
                        }
                      >
                        <td className="inst-col-check">
                          <input
                            type="checkbox"
                            checked={selectedRec.has(f.plan_id)}
                            onChange={() => toggleRecSelect(f.plan_id)}
                            aria-label={he.installments.recSelect}
                          />
                        </td>
                        <td>
                          <span className="inst-merchant">{f.merchant || '—'}</span>
                          {f.category ? <span className="inst-cat">{f.category}</span> : null}
                          {f.extra_label ? (
                            <span className="inst-extra" title={f.extra_label}>
                              {f.extra_label.length > 42
                                ? `${f.extra_label.slice(0, 40)}…`
                                : f.extra_label}
                            </span>
                          ) : null}
                          <Link
                            className="inst-explore"
                            to={exploreHref(f.merchant, f.tx_ids)}
                          >
                            {he.installments.explore}
                          </Link>
                        </td>
                        <td dir="ltr">
                          {formatMoney(f.monthly_amount)}
                          {f.amount_mode === 'variable' ? (
                            <span className="inst-amt-hint">~</span>
                          ) : null}
                        </td>
                        <td dir="ltr">{f.months_seen}</td>
                        <td dir="ltr">{f.next_expected || '—'}</td>
                        <td dir="ltr">{formatMoney(f.lifetime_paid)}</td>
                        <td>
                          <span className={`inst-badge inst-badge--${f.status}`}>
                            {statusLabelRec(f.status)}
                          </span>
                        </td>
                        <td>
                          {renderActions(
                            f.plan_id,
                            f.hidden,
                            fees.map((x) => ({
                              plan_id: x.plan_id,
                              merchant: x.merchant,
                            })),
                            { mergedSources: f.merged_sources },
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </PageShell>
  );
}
