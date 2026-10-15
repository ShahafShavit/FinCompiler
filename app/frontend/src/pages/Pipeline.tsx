import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';

import PageShell from '../components/PageShell';
import { fetchJson, getJson, postJson } from '../lib/api';
import { useBook } from '../lib/book';
import { he } from '../lib/he';
import { appendCappedLog } from '../lib/pipelineLog';
import {
  categorizeSummaryKey,
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
  const logBuf = useRef('');
  const logRaf = useRef(0);
  const runningRef = useRef(false);
  const queryClient = useQueryClient();
  const { bookId, books } = useBook();
  const apiBook = books.find((book) => book.id === bookId);
  const isApi = Boolean(apiBook?.fetcher);
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
      logBuf.current = appendCappedLog(logBuf.current, msg);
      if (logRaf.current) return;
      logRaf.current = window.requestAnimationFrame(() => {
        logRaf.current = 0;
        setLogText(logBuf.current);
        const el = logRef.current;
        if (el) el.scrollTop = el.scrollHeight;
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
        void queryClient.invalidateQueries({ queryKey: [bookId, 'ledger-meta'] });
        void queryClient.invalidateQueries({ queryKey: categorizeSummaryKey(bookId) });
      }
    },
  });

  useEffect(() => {
    return () => {
      if (logRaf.current) window.cancelAnimationFrame(logRaf.current);
    };
  }, []);

  useEffect(() => {
    if (conn === 'open') return;
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
    const id = window.setInterval(pollStatus, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [conn]);

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
    setDlM(false);
    setDlBc(true);
    setDlOsh(true);
    setDlTp(true);
    setFromD('');
    setToD('');
    setRoute(true);
    setProcH(true);
    setProcT(true);
    setIngestTp(true);
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
      {isApi ? null : (
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
      )}
      {isApi ? null : <p className="hint pipe-preset-hint">{he.pipeline.monthlyCloseHint}</p>}

      {isApi ? null : (
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
      )}

      {isApi ? <ApiSyncCard backfill={Boolean(apiBook?.backfill)} running={running} /> : null}

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
        {isApi ? <p className="hint">{he.books.sheetsPersonal}</p> : null}
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

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function messageOf(data: unknown): string {
  if (data && typeof data === 'object' && 'message' in data) {
    const message = (data as { message?: unknown }).message;
    if (typeof message === 'string' && message) return message;
  }
  return 'Request failed';
}

type SessionState = 'ready' | 'email' | 'phone' | 'password' | 'sms' | 'login';

type SessionField = {
  id: string;
  stored: boolean;
  valid: boolean;
  summary: string;
  source?: string;
};

type SessionPayload = {
  state?: SessionState;
  fields?: SessionField[];
  message?: string;
};

const summaryText: Record<string, string> = {
  missing: 'חסר',
  invalid: 'פורמט לא תקין',
  expired: 'פג תוקף',
  saved: 'שמור',
  rejected: 'הבנק דחה',
  valid: 'פעיל',
  none: 'אין',
  waiting: 'ממתין',
};

function fieldLabel(id: string): string {
  if (id === 'email') return he.books.email;
  if (id === 'phone') return he.books.phone;
  if (id === 'otpToken') return he.books.fieldOtp;
  if (id === 'accessToken') return he.books.fieldAccess;
  if (id === 'smsChallenge') return he.books.fieldChallenge;
  return id;
}

function displayError(text: string): string {
  if (text.includes('Max send attempts') || text.includes('SMS send limit')) return he.books.smsLimit;
  if (text.includes('did not send the SMS')) return he.books.smsNotSent;
  return text;
}

function ApiSyncCard({ backfill, running }: { backfill: boolean; running: boolean }) {
  const [month, setMonth] = useState(currentMonth);
  const [fromMonth, setFromMonth] = useState(currentMonth);
  const [toMonth, setToMonth] = useState(currentMonth);
  const [sessionState, setSessionState] = useState<SessionState | ''>('');
  const [fields, setFields] = useState<SessionField[] | null>(null);
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [sms, setSms] = useState('');
  const [error, setError] = useState('');
  const [smsBusy, setSmsBusy] = useState(false);
  const pending = useRef<'sync' | 'backfill' | null>(null);

  function applyStatus(data: SessionPayload) {
    if (data.state) setSessionState(data.state);
    if (data.fields) setFields(data.fields);
    setChecking(false);
  }

  useEffect(() => {
    let cancel = false;
    void (async () => {
      try {
        const data = await getJson<SessionPayload>('/api/books/session');
        if (!cancel) applyStatus(data);
      } catch (err) {
        if (cancel) return;
        setChecking(false);
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  async function prepare(extra?: { email?: string; phone?: string; force_sms?: boolean }): Promise<SessionState> {
    const r = await postJson<SessionPayload>('/api/books/session/prepare', extra ?? {});
    applyStatus(r.data);
    if (!r.ok) throw new Error(displayError(messageOf(r.data)));
    return r.data.state || 'ready';
  }

  async function startJob(action: 'sync' | 'backfill') {
    const body =
      action === 'sync'
        ? { action: 'book_sync', options: { month } }
        : { action: 'book_backfill', options: { from: fromMonth, to: toMonth } };
    const r = await postJson('/api/jobs/run', body);
    if (!r.ok) throw new Error(messageOf(r.data));
    setPassword('');
    setSms('');
    setSessionState('ready');
  }

  async function requestSms() {
    if (smsBusy) return;
    if (!password.trim()) {
      setError(he.books.passwordFirst);
      return;
    }
    setSmsBusy(true);
    setError('');
    try {
      const next = await prepare({ force_sms: true });
      setSms('');
      setSessionState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSmsBusy(false);
    }
  }

  async function begin(action: 'sync' | 'backfill') {
    setError('');
    pending.current = action;
    setChecking(true);
    try {
      const data = await getJson<SessionPayload>('/api/books/session');
      applyStatus(data);
      if (data.state === 'ready') await startJob(action);
    } catch (err) {
      setChecking(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitAuth() {
    setError('');
    try {
      if (sessionState === 'email' || sessionState === 'phone') {
        const next = await prepare({ email, phone });
        setSessionState(next);
        if (next === 'ready' && pending.current) await startJob(pending.current);
        return;
      }
      const r = await postJson<SessionPayload>('/api/books/session/complete', {
        password,
        sms_code: sms,
      });
      applyStatus(r.data);
      if (!r.ok) throw new Error(displayError(messageOf(r.data)));
      setPassword('');
      setSms('');
      if (pending.current) await startJob(pending.current);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function clearField(field: string) {
    setError('');
    const r = await postJson<SessionPayload>('/api/books/session/clear', { field });
    applyStatus(r.data);
    if (!r.ok) setError(displayError(messageOf(r.data)));
  }

  const needsAuth =
    sessionState === 'email' ||
    sessionState === 'phone' ||
    sessionState === 'password' ||
    sessionState === 'sms' ||
    sessionState === 'login';

  return (
    <section className="card">
      <h2>{he.books.sync}</h2>
      <p className="hint">{he.books.steps}</p>
      <div className="session-status" aria-live="polite">
        <h3>{he.books.statusTitle}</h3>
        {checking ? <p className="hint">{he.books.statusChecking}</p> : null}
        {fields?.map((field) => (
          <div className="session-status__row" key={field.id}>
            <span>
              {fieldLabel(field.id)}
              {': '}
              {summaryText[field.summary] || field.summary}
              {field.source === 'env' ? ` (${he.books.fromEnv})` : ''}
              {' — '}
              <span className={field.valid ? 'session-status__ok' : 'session-status__bad'}>
                {field.valid ? he.books.valid : he.books.invalid}
              </span>
            </span>
            {field.stored ? (
              <button type="button" className="secondary session-status__clear" onClick={() => void clearField(field.id)}>
                {he.books.clear}
              </button>
            ) : null}
          </div>
        ))}
        <p className="hint">{he.books.passwordNote}</p>
      </div>
      <label>
        {he.books.month}
        <input value={month} onChange={(e) => setMonth(e.target.value)} placeholder="YYYY-MM" />
      </label>
      <button type="button" disabled={running} onClick={() => void begin('sync')}>
        {he.books.sync}
      </button>
      {backfill ? (
        <div className="pipe-grid2" style={{ marginTop: '0.75rem' }}>
          <label>
            {he.books.from}
            <input value={fromMonth} onChange={(e) => setFromMonth(e.target.value)} placeholder="YYYY-MM" />
          </label>
          <label>
            {he.books.to}
            <input value={toMonth} onChange={(e) => setToMonth(e.target.value)} placeholder="YYYY-MM" />
          </label>
          <button type="button" disabled={running} onClick={() => void begin('backfill')}>
            {he.books.backfill}
          </button>
        </div>
      ) : null}
      {needsAuth ? (
        <div className="session-steps">
          {sessionState === 'password' ? <p className="hint">{he.books.passwordHint}</p> : null}
          {sessionState === 'sms' ? <p className="hint">{he.books.smsHint}</p> : null}
          {sessionState === 'login' ? <p className="hint">{he.books.loginHint}</p> : null}
          {sessionState === 'email' ? (
            <label>
              {he.books.email}
              <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
            </label>
          ) : null}
          {sessionState === 'phone' ? (
            <label>
              {he.books.phone}
              <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+972…" />
            </label>
          ) : null}
          {sessionState === 'login' || sessionState === 'password' || sessionState === 'sms' ? (
            <label>
              {he.books.password}
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </label>
          ) : null}
          {sessionState === 'sms' ? (
            <label>
              {he.books.sms}
              <input value={sms} onChange={(e) => setSms(e.target.value)} inputMode="numeric" autoComplete="one-time-code" />
            </label>
          ) : null}
          {sessionState === 'login' ? (
            <button type="button" disabled={running || smsBusy} onClick={() => void requestSms()}>
              {he.books.sendSms}
            </button>
          ) : (
            <button type="button" disabled={running} onClick={() => void submitAuth()}>
              {he.books.continue}
            </button>
          )}
        </div>
      ) : null}
      {sessionState === 'ready' ? <p className="hint">{he.books.sessionReady}</p> : null}
      {error ? (
        <p className="banner banner--err" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
