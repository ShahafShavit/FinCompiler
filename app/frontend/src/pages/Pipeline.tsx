import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { fetchJson, getJson, postJson } from '../lib/api';
import { useEventStream } from '../lib/useEventStream';

import './Pipeline.css';

type StatusResp = { running?: boolean; current_job?: string; error?: string | null };
type QueueResp = { compiled_exists?: boolean; open_count?: number };
type SheetsStatus = {
  configured?: boolean;
  pairs?: Array<{ sheet: string; local_path: string }>;
};
type ControlConfig = { categorize_url_hint?: string };

type PipelineOptions = {
  download_enabled: boolean;
  fetch_holdings: boolean;
  fetch_max_isracard: boolean;
  fetch_bank_credit: boolean;
  fetch_bank_osh: boolean;
  from_date: string | null;
  to_date: string | null;
  route_inbox: boolean;
  process_holdings: boolean;
  process_transactions: boolean;
  backup_first: boolean;
  auto_categorize: boolean;
  drop_profile: 'full' | 'batch';
};

function normalizeDropProfile(raw: string): 'full' | 'batch' {
  const v = raw.trim().toLowerCase();
  if (v === 'batch' || v.startsWith('batch')) return 'batch';
  return 'full';
}

export default function Pipeline() {
  // Pipeline checkbox state.
  const [dl, setDl] = useState(false);
  const [dlH, setDlH] = useState(false);
  const [dlM, setDlM] = useState(false);
  const [dlBc, setDlBc] = useState(false);
  const [dlOsh, setDlOsh] = useState(false);
  const [fromD, setFromD] = useState('');
  const [toD, setToD] = useState('');
  const dlPrimedRef = useRef(false);
  const [route, setRoute] = useState(true);
  const [procH, setProcH] = useState(false);
  const [procT, setProcT] = useState(false);
  const [backup, setBackup] = useState(false);
  const [auto, setAuto] = useState(false);
  const [dropProf, setDropProf] = useState('full');

  // Sheets card.
  const [sheetsForce, setSheetsForce] = useState(false);
  const [sheetsOut, setSheetsOut] = useState('');
  const [sheetsStatusText, setSheetsStatusText] = useState('');
  const [sheetsBusy, setSheetsBusy] = useState(false);

  // Live log + connection / running state.
  const [logText, setLogText] = useState('');
  const [statusText, setStatusText] = useState('');
  const [running, setRunning] = useState(false);
  const [currentJob, setCurrentJob] = useState('');
  const [busyError, setBusyError] = useState<string | null>(null);
  const [queueHint, setQueueHint] = useState<{ text: string; ok: boolean | null }>({
    text: '',
    ok: null,
  });
  const [catUrl, setCatUrl] = useState('');
  const logRef = useRef<HTMLDivElement | null>(null);
  const runningRef = useRef(false);
  const queryClient = useQueryClient();

  // Derived dependencies (mirrors `syncPipelineDeps` from the legacy HTML page).
  const dlChildrenDisabled = !dl;
  const backupDisabled = !(procT || procH);
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

  // SSE: live log + state.
  const conn = useEventStream('/api/events', {
    log: (msg) => {
      setLogText((prev) => {
        const next = prev + msg + '\n';
        // Keep memory bounded — last ~500 KB.
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
      }
    },
  });

  // Auto-scroll log.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logText]);

  // Polling: status + categorize queue.
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
    async function pollQueue() {
      try {
        const j = await getJson<QueueResp>('/categorize/api/summary');
        if (cancelled) return;
        if (!j.compiled_exists) {
          setQueueHint({ text: 'no ledger DB', ok: null });
          return;
        }
        if (j.open_count) {
          setQueueHint({ text: `${j.open_count} need category`, ok: false });
        } else {
          setQueueHint({ text: 'queue empty', ok: true });
        }
      } catch {
        // ignore
      }
    }
    void pollQueue();
    const id = setInterval(pollQueue, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Initial: sheets status + control config.
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
              (j.pairs ?? []).map((p) => `${p.sheet} → ${p.local_path}`).join(' · '),
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
    if (busyError) return { cls: 'pill err', text: 'error: ' + busyError };
    if (running) return { cls: 'pill run', text: 'running: ' + (currentJob || 'job') };
    return { cls: 'pill', text: '' };
  }, [busyError, running, currentJob]);
  const queuePill = useMemo(() => {
    if (queueHint.ok === true) return 'pill run';
    return 'pill';
  }, [queueHint.ok]);

  async function postSheetsAction(action: 'preview' | 'push') {
    setSheetsOut(action === 'preview' ? 'Loading…' : 'Running…');
    if (action === 'push') setSheetsBusy(true);
    try {
      const url = '/api/sheets/' + action;
      const body =
        action === 'preview'
          ? {}
          : { force: sheetsForce };
      const r = await postJson<unknown>(url, body);
      setSheetsOut(JSON.stringify(r.data, null, 2));
      if (r.ok && action === 'push') {
        try {
          const j = await getJson<SheetsStatus>('/api/sheets/status');
          if (!j.configured) {
            setSheetsStatusText(
              'Google Sheets: not configured — set service account path and spreadsheet id under Settings → Providers.',
            );
          } else {
            setSheetsStatusText(
              'Targets: ' +
                (j.pairs ?? []).map((p) => `${p.sheet} → ${p.local_path}`).join(' · '),
            );
          }
        } catch {
          // ignore refresh failure
        }
      }
    } catch (e) {
      setSheetsOut('Error: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      if (action === 'push') setSheetsBusy(false);
    }
  }

  async function postJob(action: 'pipeline' | 'categorize', options: object) {
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
      setStatusText('Started: ' + (j.job_id || action));
    } catch (e) {
      setStatusText('Error: ' + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function runPipeline() {
    if (!dl && !route && !procH && !procT) {
      setStatusText('Choose at least one step (download, route, or a compile option).');
      return;
    }
    const opts: PipelineOptions = {
      download_enabled: dl,
      fetch_holdings: dlH,
      fetch_max_isracard: dlM,
      fetch_bank_credit: dlBc,
      fetch_bank_osh: dlOsh,
      from_date: fromD.trim() || null,
      to_date: toD.trim() || null,
      route_inbox: route,
      process_holdings: procH,
      process_transactions: procT,
      backup_first: backup,
      auto_categorize: auto,
      drop_profile: normalizeDropProfile(dropProf),
    };
    void postJob('pipeline', opts);
  }

  async function runCategorize() {
    void postJob('categorize', {});
  }

  return (
    <div className="pipe-page">
      <h1>Finance pipeline control</h1>
      <p className="sub pipe-header">
        <span>Local dashboard for fetches, routing, compile, and categorization.</span>
        <a href="/categorize/">Categorization</a>
        <span className={queuePill}>{queueHint.text}</span>
        <span className={`pill ${conn === 'open' ? 'run' : conn === 'closed' ? 'err' : ''}`}>
          {conn === 'open' ? 'SSE connected' : conn === 'closed' ? 'SSE disconnected' : 'SSE …'}
        </span>
        <span className={busyPill.cls}>{busyPill.text}</span>
      </p>

      <div className="card">
        <h2>Pipeline</h2>
        <p className="hint">
          Check what this run should do, then click <strong>Run pipeline</strong>. Order is always:
          optional downloads → optional route → compile holdings (if checked) → compile transactions
          (if checked).
        </p>

        <label className="pipe-row">
          <input type="checkbox" checked={dl} onChange={(e) => onToggleDl(e.target.checked)} />
          <strong>Browser download</strong> — Chrome/Selenium saves exports into{' '}
          <code>data/input/</code>
        </label>
        <div className="pipe-indent">
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlH}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlH(e.target.checked)}
            />{' '}
            Bank holdings
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlM}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlM(e.target.checked)}
            />{' '}
            Max + Isracard
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlBc}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlBc(e.target.checked)}
            />{' '}
            Leumi credit export
          </label>
          <label className="pipe-row">
            <input
              type="checkbox"
              checked={dlOsh}
              disabled={dlChildrenDisabled}
              onChange={(e) => setDlOsh(e.target.checked)}
            />{' '}
            Leumi account (osh)
          </label>
          <div className="pipe-grid2" style={{ marginTop: '0.5rem' }}>
            <div>
              <label>Osh from (DD.MM.YY)</label>
              <br />
              <input
                type="text"
                className="pipe-input-text"
                value={fromD}
                placeholder="optional"
                disabled={dlChildrenDisabled}
                onChange={(e) => setFromD(e.target.value)}
              />
            </div>
            <div>
              <label>Osh to (DD.MM.YY)</label>
              <br />
              <input
                type="text"
                className="pipe-input-text"
                value={toD}
                placeholder="optional"
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
            title={
              routeForcedOn
                ? 'Required when compiling so files are sorted into pipeline inboxes first.'
                : ''
            }
          />
          <strong>Route inbox</strong> — move <code>data/input/*.xls*</code> into pipeline holdings /
          transactions folders
        </label>
        <p className="hint">
          Automatically stays on when you compile (so new downloads reach the right pipeline
          folders).
        </p>

        <label className="pipe-row">
          <input type="checkbox" checked={procH} onChange={(e) => setProcH(e.target.checked)} />
          <strong>Compile holdings</strong> → <code>data/export/compiled/holdings.csv</code>
        </label>
        <label className="pipe-row">
          <input type="checkbox" checked={procT} onChange={(e) => setProcT(e.target.checked)} />
          <strong>Compile transactions</strong> → <code>data/ledger.sqlite</code> (transactions
          ledger)
        </label>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={backup}
            disabled={backupDisabled}
            onChange={(e) => setBackup(e.target.checked)}
          />
          <strong>Backup snapshot first</strong> — copy compiled, static, and <code>web/data</code>{' '}
          into <code>data/_backups/&lt;timestamp&gt;/</code> before compile steps
        </label>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={auto}
            disabled={autoDisabled}
            onChange={(e) => setAuto(e.target.checked)}
          />
          <strong>Auto-categorize</strong> after transactions compile (rows still missing a category
          → <a href="/categorize/">/categorize/</a>)
        </label>

        <label className="pipe-row combo-row" style={{ marginTop: '0.65rem' }}>
          Transaction column-drop profile (type or pick)
          <input
            type="text"
            className="pipe-combo"
            list="drop_prof_list"
            value={dropProf}
            onChange={(e) => setDropProf(e.target.value)}
            autoComplete="off"
            title="full = same drops as desktop app; batch = smaller legacy set"
          />
          <datalist id="drop_prof_list">
            <option value="full"></option>
            <option value="batch"></option>
          </datalist>
        </label>

        <div style={{ marginTop: '0.85rem' }}>
          <button type="button" disabled={running} onClick={() => void runPipeline()}>
            Run pipeline
          </button>
        </div>
        <p className="hint">
          Manual categories: <code>{catUrl}</code>
        </p>
      </div>

      <div className="card">
        <h2>Categorization only</h2>
        <p className="hint">
          Runs an auto pass on the SQLite ledger. Open <a href="/categorize/">/categorize/</a> any
          time to answer whatever is still missing a category (no separate &quot;session&quot;).
        </p>
        <button type="button" disabled={running} onClick={() => void runCategorize()}>
          Run auto-categorize
        </button>
      </div>

      <div className="card">
        <h2>Google Sheets (push only)</h2>
        <p className="hint">
          <strong>Preview</strong> compares each worksheet to local data (holdings CSV + SQLite
          ledger export for Totals when <code>ledger.sqlite</code> exists).{' '}
          <strong>Push</strong> updates the cloud; there is no pull. Use <strong>Force</strong> to
          push even when preview reports differences. Heatmap reads from the ledger —{' '}
          <a href="/heatmap">/heatmap</a>.
        </p>
        <p className="hint">{sheetsStatusText}</p>
        <label className="pipe-row">
          <input
            type="checkbox"
            checked={sheetsForce}
            onChange={(e) => setSheetsForce(e.target.checked)}
          />
          <strong>Force</strong> — push even when preview reports problems
        </label>
        <div style={{ marginTop: '0.65rem' }}>
          <button
            type="button"
            className="secondary"
            disabled={sheetsBusy}
            onClick={() => void postSheetsAction('preview')}
          >
            Preview sync
          </button>
          <button
            type="button"
            disabled={sheetsBusy}
            onClick={() => void postSheetsAction('push')}
          >
            Push to Sheets
          </button>
        </div>
        <pre className="pipe-sheets-out" aria-live="polite">
          {sheetsOut}
        </pre>
      </div>

      <div className="card">
        <h2>Live log</h2>
        <div ref={logRef} className="pipe-log">
          {logText}
        </div>
        <p className="pipe-status">{statusText}</p>
      </div>
    </div>
  );
}
