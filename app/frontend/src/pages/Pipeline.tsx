import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';

import PageShell from '../components/PageShell';
import { fetchJson, getJson } from '../lib/api';
import { he } from '../lib/he';
import {
  CATEGORIZE_SUMMARY_KEY,
  openCountFromSummary,
  useCategorizeSummary,
} from '../lib/useCategorizeSummary';
import { useEventStream } from '../lib/useEventStream';

import './Pipeline.css';

type StatusResp = { running?: boolean; current_job?: string; error?: string | null };
type SheetsStatus = {
  configured?: boolean;
  pairs?: Array<{ sheet: string; local_source?: string }>;
  ledger_present?: boolean;
};
type ControlConfig = { categorize_url_hint?: string };

type PipelineOptions = {
  download_enabled: boolean;
  fetch_holdings: boolean;
  fetch_trade_portfolio: boolean;
  fetch_max_isracard: boolean;
  fetch_bank_credit: boolean;
  fetch_bank_osh: boolean;
  from_date: string | null;
  to_date: string | null;
  route_inbox: boolean;
  process_holdings: boolean;
  process_transactions: boolean;
  process_trade_portfolio: boolean;
  backup_first: boolean;
  auto_categorize: boolean;
};

export default function Pipeline() {
  const [dl, setDl] = useState(false);
  const [dlH, setDlH] = useState(false);
  const [dlM, setDlM] = useState(false);
  const [dlBc, setDlBc] = useState(false);
  const [dlOsh, setDlOsh] = useState(false);
  const [dlTp, setDlTp] = useState(false);
  const [fromD, setFromD] = useState('');
  const [toD, setToD] = useState('');
  const dlPrimedRef = useRef(false);
  const [route, setRoute] = useState(true);
  const [procH, setProcH] = useState(false);
  const [procT, setProcT] = useState(false);
  const [ingestTp, setIngestTp] = useState(false);
  const [backup, setBackup] = useState(false);
  const [auto, setAuto] = useState(false);

  const [sheetsForce, setSheetsForce] = useState(false);
  const [sheetsOut, setSheetsOut] = useState('');
  const [sheetsStatusText, setSheetsStatusText] = useState('');
  const [sheetsBusy, setSheetsBusy] = useState(false);

  const [logText, setLogText] = useState('');
  const [statusText, setStatusText] = useState('');
  const [running, setRunning] = useState(false);
  const [currentJob, setCurrentJob] = useState('');
  const [busyError, setBusyError] = useState<string | null>(null);
  const [catUrl, setCatUrl] = useState('');
  const logRef = useRef<HTMLDivElement | null>(null);
  const runningRef = useRef(false);
  const queryClient = useQueryClient();
  const summaryQ = useCategorizeSummary();
  const openCount = openCountFromSummary(summaryQ.data);

  const queueHint = useMemo(() => {
    if (summaryQ.isError) return { text: '', ok: null as boolean | null };
    if (summaryQ.data && summaryQ.data.compiled_exists === false) {
      return { text: he.pipeline.noLedger, ok: null as boolean | null };
    }
    if (openCount != null && openCount > 0) {
      return { text: `${openCount} ${he.pipeline.queueNeed}`, ok: false as boolean | null };
    }
    if (openCount === 0) {
      return { text: he.pipeline.queueEmpty, ok: true as boolean | null };
    }
    return { text: '', ok: null as boolean | null };
  }, [summaryQ.data, summaryQ.isError, openCount]);

  const dlChildrenDisabled = !dl;
  const backupDisabled = !(procT || procH || ingestTp);
  const autoDisabled = !procT;
  const routeForcedOn = procH || procT;

  useEffect(() => {
    if (backupDisabled && backup) setBackup(false);
  }, [backupDisabled, backup]);
  useEffect(() => {
    if (autoDisabled && auto) setAuto(false);
  }, [autoDisabled, auto]);
  useEffect(() => {
    if (routeForcedOn && !route) setRoute(true);
  }, [routeForcedOn, route]);

  const onToggleDl = (next: boolean) => {
    setDl(next);
    if (next && !dlPrimedRef.current) {
      dlPrimedRef.current = true;
      setDlH(true);
      setDlM(true);
      setDlBc(true);
      setDlOsh(true);
    }
  };

  const conn = useEventStream('/api/events', {
    log: (msg) => {
      setLogText((prev) => {
        const next = prev + msg + '\n';
        return next.length > 500_000 ? next.slice(-500_000) : next;
      });
    },
    state: (isRunning, job, err) => {
      const wasRunning = runningRef.current;
      runningRef.current = isRunning;
      setRunning(isRunning);
      setCurrentJob(job);
      if (err) setBusyError(err);
      else setBusyError(null);
      if (wasRunning && !isRunning && !err) {
        void queryClient.invalidateQueries({ queryKey: ['ledger-meta'] });
        void queryClient.invalidateQueries({ queryKey: CATEGORIZE_SUMMARY_KEY });
      }
    },
  });

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logText]);

  useEffect(() => {
    let cancelled = false;
    async function pollStatus() {
      try {
        const j = await getJson<StatusResp>('/api/status');
        if (cancelled) return;
        setRunning(!!j.running);
        setCurrentJob(j.current_job || '');
        setBusyError(j.error || null);
      } catch {
        // ignore — SSE is the primary signal
      }
    }
    void pollStatus();
    const id = setInterval(pollStatus, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadSheets() {
      try {
        const j = await getJson<SheetsStatus>('/api/sheets/status');
        if (cancelled) return;
        if (!j.configured) {
          setSheetsStatusText(
            'Google Sheets: not configured — set service account path and spreadsheet id under Settings → Providers.',
          );
        } else {
          setSheetsStatusText(
            'Targets: ' +
              (j.pairs ?? [])
                .map((p) => `${p.sheet} (${p.local_source ?? 'ledger'})`)
                .join(' · '),
          );
        }
      } catch {
        setSheetsStatusText('');
      }
    }
    async function loadConfig() {
      try {
        const j = await getJson<ControlConfig>('/api/config');
        if (!cancelled) setCatUrl(j.categorize_url_hint || '');
      } catch {
        // ignore
      }
    }
    void loadSheets();
    void loadConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const busyPill = useMemo(() => {
    if (busyError) return { cls: 'pill err', text: `${he.common.error}: ${busyError}` };
    if (running)
      return {
        cls: 'pill run',
        text: `${he.pipeline.runningJob}: ${currentJob || 'job'}`,
      };
    return { cls: 'pill', text: he.pipeline.idle };
  }, [busyError, running, currentJob]);

  const queuePill = useMemo(() => {
    if (queueHint.ok === true) return 'pill run';
    return 'pill';
  }, [queueHint.ok]);

  const ssePill = useMemo(() => {
    if (conn === 'open') return { cls: 'pill run', text: he.pipeline.sseConnected };
    if (conn === 'closed') return { cls: 'pill err', text: he.pipeline.sseDisconnected };
    return { cls: 'pill', text: he.pipeline.ssePending };
  }, [conn]);

  async function postSheetsAction(action: 'preview' | 'push') {
    setSheetsOut(action === 'preview' ? 'Queued preview…' : 'Queued push…');
    setSheetsBusy(true);
    try {
      await postJob(action === 'preview' ? 'sheets_preview' : 'sheets_push', {
        force: sheetsForce,
      });
      setSheetsOut((prev) => prev + '\nWatch the live log for Sheets output.');
    } catch (e) {
      setSheetsOut('Error: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSheetsBusy(false);
    }
  }

  async function postJob(action: string, options: object) {
    setStatusText('');
    try {
      const r = await fetchJson<{ ok?: boolean; job_id?: string; error?: string }>(
        '/api/jobs/run',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action, options }),
        },
      );
      const j = r.data;
      if (!r.ok) {
        throw new Error(j.error || `HTTP ${r.status}`);
      }
      setRunning(true);
      setCurrentJob(j.job_id || action);
      setStatusText(`${he.pipeline.started}: ${j.job_id || action}`);
    } catch (e) {
      setStatusText(`${he.common.error}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function runPipeline() {
    if (!dl && !route && !procH && !procT && !ingestTp) {
      setStatusText(he.pipeline.chooseStep);
      return;
    }
    if (dl && !window.confirm(he.pipeline.confirmDownload)) {
      return;
    }
    const opts: PipelineOptions = {
      download_enabled: dl,
      fetch_holdings: dlH,
      fetch_trade_portfolio: dlTp,
      fetch_max_isracard: dlM,
      fetch_bank_credit: dlBc,
      fetch_bank_osh: dlOsh,
      from_date: fromD.trim() || null,
      to_date: toD.trim() || null,
      route_inbox: route,
      process_holdings: procH,
      process_transactions: procT,
      process_trade_portfolio: ingestTp,
      backup_first: backup,
      auto_categorize: auto,
    };
    void postJob('pipeline', opts);
  }

  async function runCategorize() {
    void postJob('categorize', {});
  }

  function applyMonthlyClosePreset() {
    dlPrimedRef.current = true;
    setDl(true);
    setDlH(true);
    setDlM(true);
    setDlBc(true);
    setDlOsh(true);
    setDlTp(false);
    setRoute(true);
    setProcH(true);
    setProcT(true);
    setIngestTp(false);
    setBackup(true);
    setAuto(true);
    setStatusText(he.pipeline.monthlyCloseHint);
  }

  const headerActions = (
    <div className="pipe-status-row">
      <Link to="/categorize" className="pipe-cat-link">
        {he.pipeline.openCategorize}
      </Link>
      <span className={queuePill}>{queueHint.text}</span>
      {openCount != null && openCount > 0 ? (
        <Link to="/categorize" className="pipe-cat-link pipe-cat-link--need">
          {he.pipeline.goCategorize}
        </Link>
      ) : null}
      <span className={ssePill.cls}>{ssePill.text}</span>
      <span className={busyPill.cls}>{busyPill.text}</span>
    </div>
  );

  return (
    <PageShell
      title={he.pipeline.title}
      sub={he.pipeline.sub}
      width="narrow"
      actions={headerActions}
      className="pipe-shell"
    >
      <div className="pipe-sticky-run">
        <button type="button" className="pipe-run-btn" disabled={running} onClick={() => void runPipeline()}>
          {running ? he.pipeline.busy : he.pipeline.run}
        </button>
        <button
          type="button"
          className="secondary pipe-preset-btn"
          disabled={running}
          onClick={applyMonthlyClosePreset}
          title={he.pipeline.monthlyCloseHint}
        >
          {he.pipeline.monthlyClose}
        </button>
      </div>
      <p className="hint pipe-preset-hint">{he.pipeline.monthlyCloseHint}</p>

      <section className="card">
        <h2>{he.pipeline.pipeline}</h2>
        <p className="hint">{he.pipeline.hintOrder}</p>

        <label className="pipe-row">
          <input type="checkbox" checked={dl} onChange={(e) => onToggleDl(e.target.checked)} />
          <span>
            <strong>{he.pipeline.download}</strong> — {he.pipeline.downloadHint} <code>data/input/</code>
          </span>
        </label>
        <div className="pipe-indent">
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlH}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlH(e.target.checked)}
            />{' '}
            {he.pipeline.bankHoldings}
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlM}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlM(e.target.checked)}
            />{' '}
            {he.pipeline.maxIsracard}
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlBc}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlBc(e.target.checked)}
            />{' '}
            {he.pipeline.leumiCredit}
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlOsh}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlOsh(e.target.checked)}
            />{' '}
            {he.pipeline.leumiOsh}
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlTp}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlTp(e.target.checked)}
            />{' '}
            {he.pipeline.leumiTrade}
          </label>
          <div className="pipe-grid2" style={{ marginTop: '0.5rem' }}>
            <div>
              <label>{he.pipeline.oshFrom}</label>
              <br />
              <input
                type="text"
                className="pipe-input-text"
                value={fromD}
                placeholder={he.pipeline.optional}
                disabled={dlChildrenDisabled}
                onChange={(e) => setFromD(e.target.value)}
              />
            </div>
            <div>
              <label>{he.pipeline.oshTo}</label>
              <br />
              <input
                type="text"
                className="pipe-input-text"
                value={toD}
                placeholder={he.pipeline.optional}
                disabled={dlChildrenDisabled}
                onChange={(e) => setToD(e.target.value)}
              />
            </div>
          </div>
        </div>

        <label className="pipe-row" style={{ marginTop: '0.75rem' }}>
          <input
            type="checkbox"
            checked={route}
            disabled={routeForcedOn}
            onChange={(e) => setRoute(e.target.checked)}
            title={routeForcedOn ? he.pipeline.routeForced : ''}
          />
          <span>
            <strong>{he.pipeline.routeInbox}</strong> — {he.pipeline.routeHint}
          </span>
        </label>
        <p className="hint">{he.pipeline.routeForced}</p>

        <label className="pipe-row">
          <input
            type="checkbox"
            checked={ingestTp}
            onChange={(e) => setIngestTp(e.target.checked)}
          />
          <span>
            <strong>{he.pipeline.compilePortfolio}</strong> → <code>data/ledger.sqlite</code>{' '}
            (trade_portfolio_position)
          </span>
        </label>

        <label className="pipe-row">
          <input type="checkbox" checked={procH} onChange={(e) => setProcH(e.target.checked)} />
          <span>
            <strong>{he.pipeline.compileHoldings}</strong> → <code>data/ledger.sqlite</code>{' '}
            (holdings_balance)
          </span>
        </label>
        <label className="pipe-row">
          <input type="checkbox" checked={procT} onChange={(e) => setProcT(e.target.checked)} />
          <span>
            <strong>{he.pipeline.compileTx}</strong> → <code>data/ledger.sqlite</code> (transactions)
          </span>
        </label>
        <p className="hint pipe-indent">
          {he.pipeline.dropRulesHint} (<code>data/private/transaction_drop_rules.json</code>).
        </p>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={backup}
            disabled={backupDisabled}
            onChange={(e) => setBackup(e.target.checked)}
          />
          <span>
            <strong>{he.pipeline.backupFirst}</strong> — {he.pipeline.backupHint}{' '}
            <code>data/_backups/&lt;timestamp&gt;/</code>
          </span>
        </label>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={auto}
            disabled={autoDisabled}
            onChange={(e) => setAuto(e.target.checked)}
          />
          <span>
            <strong>{he.pipeline.autoCategorize}</strong> →{' '}
            <Link to="/categorize">{he.pipeline.openCategorize}</Link>
          </span>
        </label>

        {catUrl ? (
          <p className="hint">
            <code>{catUrl}</code>
          </p>
        ) : null}
      </section>

      <section className="card">
        <h2>{he.pipeline.categorizeOnly}</h2>
        <p className="hint">{he.pipeline.categorizeCardHint}</p>
        <button type="button" disabled={running} onClick={() => void runCategorize()}>
          {he.pipeline.runCategorize}
        </button>
      </section>

      <section className="card">
        <h2>{he.pipeline.sheets}</h2>
        <p className="hint">{he.pipeline.sheetsHint}</p>
        <p className="hint">{sheetsStatusText}</p>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={sheetsForce}
            onChange={(e) => setSheetsForce(e.target.checked)}
          />
          <strong>{he.pipeline.force}</strong>
        </label>
        <div className="pipe-sheets-actions">
          <button
            type="button"
            className="secondary"
            disabled={sheetsBusy}
            onClick={() => void postSheetsAction('preview')}
          >
            {he.pipeline.previewSync}
          </button>
          <button type="button" disabled={sheetsBusy} onClick={() => void postSheetsAction('push')}>
            {he.pipeline.pushSheets}
          </button>
        </div>
        <pre className="pipe-sheets-out" aria-live="polite">
          {sheetsOut}
        </pre>
      </section>

      <section className="card pipe-log-dock" aria-label={he.pipeline.liveLog}>
        <h2>{he.pipeline.liveLog}</h2>
        <div ref={logRef} className="pipe-log">
          {logText}
        </div>
        <p className="pipe-status">{statusText}</p>
      </section>
    </PageShell>
  );
}
