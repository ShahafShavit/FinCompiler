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

type DropRuleRow = { key: string; column: string; value: string };

const DEFAULT_DROP_COLUMN = 'מקור עסקה';

function newDropRuleKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `r-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

function emptyDropRuleRow(): DropRuleRow {
  return { key: newDropRuleKey(), column: DEFAULT_DROP_COLUMN, value: '' };
}

function rowsFromDropRulesApi(data: Record<string, unknown>): DropRuleRow[] {
  const rules = data.rules;
  if (!Array.isArray(rules)) return [];
  const out: DropRuleRow[] = [];
  for (const item of rules) {
    if (!item || typeof item !== 'object') continue;
    const o = item as Record<string, unknown>;
    const col = typeof o.column === 'string' ? o.column : String(o.column ?? '');
    const val = typeof o.value === 'string' ? o.value : String(o.value ?? '');
    out.push({ key: newDropRuleKey(), column: col, value: val });
  }
  return out;
}

function validateDropRuleRowsForSave(rows: DropRuleRow[]): string | null {
  const problems: string[] = [];
  rows.forEach((r, i) => {
    const c = r.column.trim();
    const v = r.value.trim();
    if (c && !v) problems.push(`Row ${i + 1}: value is required when column is set.`);
    if (!c && v) problems.push(`Row ${i + 1}: column is required when value is set.`);
  });
  return problems.length ? problems.join(' ') : null;
}

function buildDropRulesPayload(rows: DropRuleRow[]): {
  version: 1;
  rules: { column: string; value: string }[];
} {
  const rules = rows
    .map((r) => ({ column: r.column.trim(), value: r.value.trim() }))
    .filter((r) => r.column.length > 0 && r.value.length > 0);
  return { version: 1, rules };
}

function rowLooksIncomplete(r: DropRuleRow): boolean {
  const c = r.column.trim();
  const v = r.value.trim();
  return (c.length > 0 && v.length === 0) || (c.length === 0 && v.length > 0);
}

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

  const [dropRulesRows, setDropRulesRows] = useState<DropRuleRow[]>([]);
  const [dropRulesAdvancedJson, setDropRulesAdvancedJson] = useState('');
  const [dropRulesErr, setDropRulesErr] = useState<string | null>(null);
  const [dropRulesMsg, setDropRulesMsg] = useState<string | null>(null);

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

      setDropRulesErr(null);
      setDropRulesMsg(null);
      const dr = await fetchJson<Record<string, unknown>>('/api/transaction-drop-rules', {
        cache: 'no-store',
        signal: ctrl.signal,
      });
      if (dr.ok && dr.data && typeof dr.data === 'object') {
        const doc = dr.data as Record<string, unknown>;
        const next = rowsFromDropRulesApi(doc);
        setDropRulesRows(next.length > 0 ? next : [emptyDropRuleRow()]);
        setDropRulesAdvancedJson(JSON.stringify(buildDropRulesPayload(next.length > 0 ? next : [emptyDropRuleRow()]), null, 2));
      } else {
        setDropRulesRows([emptyDropRuleRow()]);
        setDropRulesAdvancedJson(JSON.stringify({ version: 1, rules: [] }, null, 2));
        setDropRulesErr(
          `Transaction drop rules: failed to load (${dr.status}). ${JSON.stringify(dr.data).slice(0, 200)}`,
        );
      }
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

  function syncAdvancedJsonFromRows(rows: DropRuleRow[]) {
    setDropRulesAdvancedJson(JSON.stringify(buildDropRulesPayload(rows), null, 2));
  }

  function applyJsonToDropRulesTable() {
    setDropRulesErr(null);
    setDropRulesMsg(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(dropRulesAdvancedJson || '{}');
    } catch {
      setDropRulesErr('Advanced JSON: invalid JSON.');
      return;
    }
    if (!parsed || typeof parsed !== 'object') {
      setDropRulesErr('Advanced JSON: root must be an object.');
      return;
    }
    const o = parsed as Record<string, unknown>;
    if (o.version !== undefined && Number(o.version) !== 1) {
      setDropRulesErr('Advanced JSON: only version 1 is supported.');
      return;
    }
    if (!Array.isArray(o.rules)) {
      setDropRulesErr('Advanced JSON: missing or invalid "rules" array.');
      return;
    }
    const next = rowsFromDropRulesApi(o);
    const rowsToSet = next.length > 0 ? next : [emptyDropRuleRow()];
    setDropRulesRows(rowsToSet);
    setDropRulesAdvancedJson(JSON.stringify(buildDropRulesPayload(rowsToSet), null, 2));
    setDropRulesMsg('Table updated from JSON.');
  }

  async function saveDropRules() {
    setDropRulesErr(null);
    setDropRulesMsg(null);
    const invalid = validateDropRuleRowsForSave(dropRulesRows);
    if (invalid) {
      setDropRulesErr(invalid);
      return;
    }
    const body = buildDropRulesPayload(dropRulesRows);
    if (body.rules.length === 0) {
      const ok = window.confirm(
        'Save with zero rules? No workbook rows will be dropped by this list at compile time.',
      );
      if (!ok) return;
    }
    const r = await putJson<{ ok?: boolean; error?: string; message?: string; config?: Record<string, unknown> }>(
      '/api/transaction-drop-rules',
      body,
    );
    if (!r.ok || !r.data.ok) {
      setDropRulesErr((r.data as { message?: string }).message || `Save failed (${r.status})`);
      return;
    }
    setDropRulesMsg('Transaction drop rules saved.');
    if (r.data.config && typeof r.data.config === 'object') {
      const next = rowsFromDropRulesApi(r.data.config as Record<string, unknown>);
      setDropRulesRows(next.length > 0 ? next : [emptyDropRuleRow()]);
      syncAdvancedJsonFromRows(next.length > 0 ? next : [emptyDropRuleRow()]);
    }
  }

  function updateDropRuleRow(key: string, patch: Partial<Pick<DropRuleRow, 'column' | 'value'>>) {
    setDropRulesRows((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function removeDropRuleRow(key: string) {
    setDropRulesRows((rows) => {
      const next = rows.filter((r) => r.key !== key);
      return next.length > 0 ? next : [emptyDropRuleRow()];
    });
  }

  function moveDropRuleRow(key: string, dir: -1 | 1) {
    setDropRulesRows((rows) => {
      const i = rows.findIndex((r) => r.key === key);
      if (i < 0) return rows;
      const j = i + dir;
      if (j < 0 || j >= rows.length) return rows;
      const copy = [...rows];
      const t = copy[i];
      copy[i] = copy[j];
      copy[j] = t;
      return copy;
    });
  }

  function addDropRuleRow() {
    setDropRulesRows((rows) => [...rows, emptyDropRuleRow()]);
  }

  function removeEmptyDropRuleRows() {
    setDropRulesRows((rows) => {
      const kept = rows.filter((r) => r.column.trim() !== '' || r.value.trim() !== '');
      return kept.length > 0 ? kept : [emptyDropRuleRow()];
    });
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
        <h1 style={{ margin: 0, fontSize: '1.25rem' }}>Providers, pipeline rules &amp; secrets</h1>
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
      {dropRulesErr ? <p className="settings-error">{dropRulesErr}</p> : null}
      {dropRulesMsg ? <p className="settings-msg">{dropRulesMsg}</p> : null}

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
        <h2>Transaction drop rules</h2>
        <p className="settings-hint">
          Stored in <code>data/private/transaction_drop_rules.json</code> (created on first use). Each rule removes
          workbook rows where the column exactly equals the value before compile. Wildcards are not special.
        </p>
        <div className="settings-drop-rules-toolbar">
          <button type="button" className="settings-btn-secondary" onClick={addDropRuleRow}>
            Add rule
          </button>
          <button type="button" className="settings-btn-secondary" onClick={removeEmptyDropRuleRows}>
            Remove empty rows
          </button>
        </div>
        {dropRulesRows.length === 0 ? (
          <p className="settings-drop-rules-empty">No rows loaded yet.</p>
        ) : (
          <div className="settings-drop-rules-list">
            {dropRulesRows.map((row, idx) => (
              <div
                key={row.key}
                className={`settings-drop-rule-row${rowLooksIncomplete(row) ? ' settings-drop-rule-row--invalid' : ''}`}
              >
                <span className="settings-drop-rule-num">Rule {idx + 1}</span>
                <div className="settings-drop-rule-fields">
                  <label className="settings-label" style={{ margin: 0 }}>
                    Column
                    <input
                      className="pipe-input-text settings-input-wide"
                      spellCheck={false}
                      value={row.column}
                      onChange={(e) => updateDropRuleRow(row.key, { column: e.target.value })}
                      placeholder="e.g. מקור עסקה"
                    />
                  </label>
                  <label className="settings-label" style={{ margin: 0 }}>
                    Value
                    <input
                      className="pipe-input-text settings-input-wide mono"
                      spellCheck={false}
                      value={row.value}
                      onChange={(e) => updateDropRuleRow(row.key, { value: e.target.value })}
                      placeholder="exact cell text to drop"
                    />
                  </label>
                </div>
                <div className="settings-drop-rule-actions">
                  <button
                    type="button"
                    className="settings-btn-secondary"
                    title="Move up"
                    disabled={idx === 0}
                    onClick={() => moveDropRuleRow(row.key, -1)}
                  >
                    Up
                  </button>
                  <button
                    type="button"
                    className="settings-btn-secondary"
                    title="Move down"
                    disabled={idx === dropRulesRows.length - 1}
                    onClick={() => moveDropRuleRow(row.key, 1)}
                  >
                    Down
                  </button>
                  <button type="button" className="settings-btn-secondary" onClick={() => removeDropRuleRow(row.key)}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="settings-actions" style={{ marginTop: '0.75rem' }}>
          <button type="button" className="settings-btn-primary" onClick={() => void saveDropRules()}>
            Save drop rules
          </button>
        </div>
        <details className="settings-drop-rules-advanced">
          <summary>Advanced: edit as JSON</summary>
          <div className="settings-drop-rules-advanced-inner">
            <p className="settings-hint" style={{ marginTop: 0 }}>
              Paste or edit the full document, then apply to the table. Save drop rules still sends the table state
              (use Save after applying if needed).
            </p>
            <div className="settings-drop-rules-toolbar">
              <button
                type="button"
                className="settings-btn-secondary"
                onClick={() => syncAdvancedJsonFromRows(dropRulesRows)}
              >
                Load current rules into editor
              </button>
              <button type="button" className="settings-btn-secondary" onClick={applyJsonToDropRulesTable}>
                Apply JSON to table
              </button>
            </div>
            <textarea
              value={dropRulesAdvancedJson}
              onChange={(e) => setDropRulesAdvancedJson(e.target.value)}
              spellCheck={false}
              aria-label="Transaction drop rules JSON"
            />
          </div>
        </details>
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
