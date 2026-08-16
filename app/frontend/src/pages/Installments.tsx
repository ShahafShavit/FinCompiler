import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import PageShell from '../components/PageShell';
import { fetchJson, formatMoney, patchJson } from '../lib/api';
import { he } from '../lib/he';

import './Installments.css';

type TimelineEntry = {
  month: string;
  x: number;
  amount: number | null;
  state: 'observed' | 'expected' | 'missing' | 'before_cutoff' | string;
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
};

type RecurringFee = {
  plan_id: string;
  merchant: string;
  category?: string;
  monthly_amount: number;
  months_seen: number;
  first_seen?: string;
  last_seen?: string;
  next_expected: string | null;
  lifetime_paid: number;
  last4s?: string[];
  status: string;
  hidden?: boolean;
  tx_ids?: number[];
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

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q.trim());
    if (includeHidden) params.set('include_hidden', '1');
    try {
      const r = await fetchJson<PlansResponse>(`/api/installment-plans?${params}`, {
        cache: 'no-store',
      });
      if (!r.ok || r.data.ok === false) {
        setErr(r.data.message || r.data.error || he.installments.error);
        setData(null);
      } else {
        setData(r.data);
      }
    } catch {
      setErr(he.installments.error);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [q, includeHidden]);

  useEffect(() => {
    void load();
  }, [load]);

  const patchPlan = async (planId: string, body: Record<string, unknown>) => {
    setBusyId(planId);
    try {
      const enc = encodeURIComponent(planId);
      const r = await patchJson<PlansResponse>(`/api/installment-plans/${enc}`, body);
      if (!r.ok || r.data.ok === false) {
        setErr(r.data.message || r.data.error || he.installments.error);
      } else {
        // Reload with current filters (patch returns include_hidden=true always)
        await load();
        setMergeFor(null);
      }
    } catch {
      setErr(he.installments.error);
    } finally {
      setBusyId(null);
    }
  };

  const plans = data?.installments?.plans || [];
  const fees = data?.recurring?.fees || [];
  const instSummary = data?.installments?.summary;
  const recSummary = data?.recurring?.summary;

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
    </div>
  );

  const renderActions = (
    planId: string,
    hidden: boolean | undefined,
    peers: { plan_id: string; merchant: string }[],
    opts?: { settled?: boolean; showSettle?: boolean },
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
                                { settled: p.settled, showSettle: true },
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
                <div className="inst-kpi__label">{he.installments.recKpiLapsed}</div>
                <div className="inst-kpi__value" dir="ltr">
                  {recSummary?.lapsed ?? 0}
                </div>
              </div>
            </div>

            {filteredFees.length === 0 ? (
              <p className="inst-msg">{he.installments.empty}</p>
            ) : (
              <div className="inst-table-wrap">
                <table className="inst-table">
                  <thead>
                    <tr>
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
                              : undefined
                        }
                      >
                        <td>
                          <span className="inst-merchant">{f.merchant || '—'}</span>
                          {f.category ? <span className="inst-cat">{f.category}</span> : null}
                          <Link
                            className="inst-explore"
                            to={exploreHref(f.merchant, f.tx_ids)}
                          >
                            {he.installments.explore}
                          </Link>
                        </td>
                        <td dir="ltr">{formatMoney(f.monthly_amount)}</td>
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
