import { useCallback, useEffect, useState } from 'react';

import { fetchJson, postJson, putJson } from '../lib/api';

import './Pipeline.css';
import './Settings.css';

type CredApi = {
  username: string;
  password_set: boolean;
  last6_set?: boolean;
};

type ProvidersApi = {
  version: number;
  bank: { provider: string; credentials: CredApi };
  credit_cards: Array<{ id: string; enabled: boolean; credentials: CredApi }>;
  google_sheets: { service_account_json_path: string; worksheet_id: string };
  providers_file?: string;
};

const BANK_OPTIONS = [{ id: 'leumi', label: 'Bank Leumi (Leumi)' }];

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [cfg, setCfg] = useState<ProvidersApi | null>(null);

  const [bankProvider, setBankProvider] = useState('leumi');
  const [bankUser, setBankUser] = useState('');
  const [bankPass, setBankPass] = useState('');

  const [maxEn, setMaxEn] = useState(true);
  const [maxUser, setMaxUser] = useState('');
  const [maxPass, setMaxPass] = useState('');

  const [isrEn, setIsrEn] = useState(true);
  const [isrUser, setIsrUser] = useState('');
  const [isrPass, setIsrPass] = useState('');
  const [isrLast6, setIsrLast6] = useState('');

  const [gPath, setGPath] = useState('');
  const [gSheet, setGSheet] = useState('');

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 20_000);
    try {
      const r = await fetchJson<ProvidersApi>('/api/providers-config', {
        cache: 'no-store',
        signal: ctrl.signal,
      });
      if (!r.ok) {
        const hint =
          r.status === 404
            ? ' Is the control server running the latest backend (GET /api/providers-config)?'
            : '';
        setErr(
          `Failed to load providers (${r.status}).${hint} ${JSON.stringify(r.data).slice(0, 200)}`,
        );
        return;
      }
      const data = r.data;
      if (!data?.bank?.credentials || !Array.isArray(data.credit_cards) || !data.google_sheets) {
        setErr('Invalid response from /api/providers-config (unexpected JSON shape).');
        return;
      }
      setCfg(data);
      setBankProvider(data.bank.provider || 'leumi');
      setBankUser(data.bank.credentials.username || '');
      setBankPass('');
      const maxC = data.credit_cards.find((c) => c.id === 'max');
      const isrC = data.credit_cards.find((c) => c.id === 'isracard');
      setMaxEn(maxC?.enabled ?? true);
      setMaxUser(maxC?.credentials.username || '');
      setMaxPass('');
      setIsrEn(isrC?.enabled ?? true);
      setIsrUser(isrC?.credentials.username || '');
      setIsrPass('');
      setIsrLast6('');
      setGPath(data.google_sheets.service_account_json_path || '');
      setGSheet(data.google_sheets.worksheet_id || '');
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        setErr(
          'Request timed out. If you use the Vite dev server (port 5173), ensure python -m api.main is running on 8780.',
        );
      } else {
        setErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      window.clearTimeout(t);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setErr(null);
    setMsg(null);
    const body: Record<string, unknown> = {
      bank: {
        provider: bankProvider,
        credentials: {
          username: bankUser,
          ...(bankPass.trim() !== '' ? { password: bankPass } : {}),
        },
      },
      credit_cards: [
        {
          id: 'max',
          enabled: maxEn,
          credentials: {
            username: maxUser,
            ...(maxPass.trim() !== '' ? { password: maxPass } : {}),
          },
        },
        {
          id: 'isracard',
          enabled: isrEn,
          credentials: {
            username: isrUser,
            ...(isrPass.trim() !== '' ? { password: isrPass } : {}),
            ...(isrLast6.trim() !== '' ? { last6: isrLast6 } : {}),
          },
        },
      ],
      google_sheets: {
        service_account_json_path: gPath,
        worksheet_id: gSheet,
      },
    };

    const r = await putJson<{ ok?: boolean; error?: string; message?: string; config?: ProvidersApi }>(
      '/api/providers-config',
      body,
    );
    if (!r.ok || !r.data.ok) {
      setErr((r.data as { message?: string }).message || `Save failed (${r.status})`);
      return;
    }
    setMsg('Saved.');
    if (r.data.config) setCfg(r.data.config);
    setBankPass('');
    setMaxPass('');
    setIsrPass('');
    setIsrLast6('');
  }

  async function importEnv() {
    setErr(null);
    setMsg(null);
    const r = await postJson<{
      ok?: boolean;
      message?: string;
      config?: ProvidersApi;
    }>('/api/providers/import-env', {});
    if (!r.ok || !r.data.ok) {
      setErr((r.data as { message?: string }).message || `Import failed (${r.status})`);
      return;
    }
    setMsg(r.data.message || 'Imported.');
    if (r.data.config) {
      setCfg(r.data.config);
      await load();
    }
  }

  return (
    <div className="pipe-page settings-page">
      <header className="pipe-header">
        <h1 style={{ margin: 0, fontSize: '1.25rem' }}>Providers &amp; secrets</h1>
      </header>
      {loading ? (
        <p className="pipe-status settings-loading-banner" role="status">
          Loading configuration from the server…
        </p>
      ) : null}
      <p className="settings-hint">
        Passwords are stored in <code>data/private/providers.json</code> (created on first save). Leave password
        fields empty to keep the current value. Use &quot;Import from .env&quot; once if you are migrating from
        legacy environment variables.
      </p>
      {cfg?.providers_file ? (
        <p className="settings-path">
          <span className="muted">Resolved file:</span> <code>{cfg.providers_file}</code>
        </p>
      ) : null}
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <p className="settings-msg">{msg}</p> : null}

      <section className="settings-section card">
        <h2>Bank portal</h2>
        <label className="settings-label">
          Provider
          <select
            className="pipe-combo"
            value={bankProvider}
            onChange={(e) => setBankProvider(e.target.value)}
          >
            {BANK_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="settings-label">
          Username
          <input
            className="pipe-input-text settings-input-wide"
            autoComplete="off"
            value={bankUser}
            onChange={(e) => setBankUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          Password {cfg?.bank?.credentials?.password_set ? <span className="muted">(set)</span> : null}
          <input
            className="pipe-input-text settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={bankPass}
            onChange={(e) => setBankPass(e.target.value)}
            placeholder="leave blank to keep"
          />
        </label>
      </section>

      <section className="settings-section card">
        <h2>Max (credit)</h2>
        <label className="pipe-row">
          <input type="checkbox" checked={maxEn} onChange={(e) => setMaxEn(e.target.checked)} />
          <span>Enabled for fetch</span>
        </label>
        <label className="settings-label">
          Username
          <input
            className="pipe-input-text settings-input-wide"
            autoComplete="off"
            value={maxUser}
            onChange={(e) => setMaxUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          Password{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'max')?.credentials?.password_set ? (
            <span className="muted">(set)</span>
          ) : null}
          <input
            className="pipe-input-text settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={maxPass}
            onChange={(e) => setMaxPass(e.target.value)}
            placeholder="leave blank to keep"
          />
        </label>
      </section>

      <section className="settings-section card">
        <h2>Isracard</h2>
        <label className="pipe-row">
          <input type="checkbox" checked={isrEn} onChange={(e) => setIsrEn(e.target.checked)} />
          <span>Enabled for fetch</span>
        </label>
        <label className="settings-label">
          Username
          <input
            className="pipe-input-text settings-input-wide"
            autoComplete="off"
            value={isrUser}
            onChange={(e) => setIsrUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          Password{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'isracard')?.credentials?.password_set ? (
            <span className="muted">(set)</span>
          ) : null}
          <input
            className="pipe-input-text settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={isrPass}
            onChange={(e) => setIsrPass(e.target.value)}
            placeholder="leave blank to keep"
          />
        </label>
        <label className="settings-label">
          Last 6 digits{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'isracard')?.credentials?.last6_set ? (
            <span className="muted">(set)</span>
          ) : null}
          <input
            className="pipe-input-text settings-input-narrow"
            autoComplete="off"
            value={isrLast6}
            onChange={(e) => setIsrLast6(e.target.value)}
            placeholder="leave blank to keep"
            maxLength={6}
          />
        </label>
      </section>

      <section className="settings-section card">
        <h2>Google Sheets</h2>
        <label className="settings-label">
          Service account JSON path
          <input
            className="pipe-input-text settings-input-wide"
            value={gPath}
            onChange={(e) => setGPath(e.target.value)}
            placeholder="e.g. data/static/your-project.json"
          />
        </label>
        <label className="settings-label">
          Spreadsheet ID
          <input
            className="pipe-input-text settings-input-wide"
            value={gSheet}
            onChange={(e) => setGSheet(e.target.value)}
          />
        </label>
      </section>

      <div className="settings-actions">
        <button type="button" className="settings-btn-primary" onClick={() => void save()}>
          Save
        </button>
        <button type="button" className="settings-btn-secondary" onClick={() => void load()}>
          Reload
        </button>
        <button type="button" className="settings-btn-secondary" onClick={() => void importEnv()}>
          Import from .env
        </button>
      </div>
    </div>
  );
}
