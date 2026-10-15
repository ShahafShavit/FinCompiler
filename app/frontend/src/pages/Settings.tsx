import { useCallback, useEffect, useState } from 'react';

import PageShell from '../components/PageShell';
import { fetchJson, postJson, putJson } from '../lib/api';
import { he } from '../lib/he';

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
  investment_portfolio?: { enabled: boolean };
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
  const [invPortEn, setInvPortEn] = useState(true);

  const [dropRulesRows, setDropRulesRows] = useState<DropRuleRow[]>([]);
  const [dropRulesAdvancedJson, setDropRulesAdvancedJson] = useState('');
  const [dropRulesErr, setDropRulesErr] = useState<string | null>(null);
  const [dropRulesMsg, setDropRulesMsg] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setErr(null);
    setLoading(true);
    const timeout = new AbortController();
    const t = window.setTimeout(() => timeout.abort(), 20_000);
    const onAbort = () => timeout.abort();
    signal?.addEventListener('abort', onAbort);
    try {
      const r = await fetchJson<ProvidersApi>('/api/providers-config', {
        cache: 'no-store',
        signal: timeout.signal,
      });
      if (signal?.aborted) return;
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
      setInvPortEn(data.investment_portfolio?.enabled ?? true);

      setDropRulesErr(null);
      setDropRulesMsg(null);
      const dr = await fetchJson<Record<string, unknown>>('/api/transaction-drop-rules', {
        cache: 'no-store',
        signal: timeout.signal,
      });
      if (signal?.aborted) return;
      if (dr.ok && dr.data && typeof dr.data === 'object') {
        const doc = dr.data as Record<string, unknown>;
        const next = rowsFromDropRulesApi(doc);
        setDropRulesRows(next.length > 0 ? next : [emptyDropRuleRow()]);
        setDropRulesAdvancedJson(
          JSON.stringify(
            buildDropRulesPayload(next.length > 0 ? next : [emptyDropRuleRow()]),
            null,
            2,
          ),
        );
      } else {
        setDropRulesRows([emptyDropRuleRow()]);
        setDropRulesAdvancedJson(JSON.stringify({ version: 1, rules: [] }, null, 2));
        setDropRulesErr(
          `Transaction drop rules: failed to load (${dr.status}). ${JSON.stringify(dr.data).slice(0, 200)}`,
        );
      }
    } catch (e) {
      if (signal?.aborted) return;
      if (e instanceof Error && e.name === 'AbortError') {
        setErr(
          'Request timed out. If you use the Vite dev server (port 5173), ensure python -m api.main is running on 8780.',
        );
      } else {
        setErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      window.clearTimeout(t);
      signal?.removeEventListener('abort', onAbort);
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  async function save() {
    if (!window.confirm(he.settings.confirmSaveProviders)) return;
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
        ...(gPath !== (cfg?.google_sheets.service_account_json_path || '')
          ? { service_account_json_path: gPath }
          : {}),
        worksheet_id: gSheet,
      },
      investment_portfolio: { enabled: invPortEn },
    };

    const r = await putJson<{
      ok?: boolean;
      error?: string;
      message?: string;
      config?: ProvidersApi;
    }>('/api/providers-config', body);
    if (!r.ok || !r.data.ok) {
      setErr((r.data as { message?: string }).message || `Save failed (${r.status})`);
      return;
    }
    setMsg(he.settings.saved);
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
    setDropRulesMsg(he.settings.tableFromJson);
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
      const ok = window.confirm(he.settings.confirmZeroRules);
      if (!ok) return;
    }
    const r = await putJson<{
      ok?: boolean;
      error?: string;
      message?: string;
      config?: Record<string, unknown>;
    }>('/api/transaction-drop-rules', body);
    if (!r.ok || !r.data.ok) {
      setDropRulesErr((r.data as { message?: string }).message || `Save failed (${r.status})`);
      return;
    }
    setDropRulesMsg(he.settings.dropRulesSaved);
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
    if (!window.confirm(he.settings.confirmImportEnv)) return;
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
    <PageShell title={he.settings.title} sub={he.settings.sub} width="narrow">
      {loading ? (
        <p className="settings-loading-banner" role="status">
          {he.settings.loading}
        </p>
      ) : null}
      <p className="settings-hint">{he.settings.hint}</p>
      {cfg?.providers_file ? (
        <p className="settings-path">
          <span className="muted">{he.settings.resolvedFile}:</span> <code>{cfg.providers_file}</code>
        </p>
      ) : null}
      {err ? <p className="settings-error">{err}</p> : null}
      {msg ? <p className="settings-msg">{msg}</p> : null}
      {dropRulesErr ? <p className="settings-error">{dropRulesErr}</p> : null}
      {dropRulesMsg ? <p className="settings-msg">{dropRulesMsg}</p> : null}

      <section className="card">
        <h2>{he.settings.bankPortal}</h2>
        <label className="settings-label">
          {he.settings.provider}
          <select value={bankProvider} onChange={(e) => setBankProvider(e.target.value)}>
            {BANK_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="settings-label">
          {he.settings.username}
          <input
            className="settings-input-wide"
            autoComplete="off"
            value={bankUser}
            onChange={(e) => setBankUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          {he.settings.password}{' '}
          {cfg?.bank?.credentials?.password_set ? (
            <span className="muted">{he.settings.passwordSet}</span>
          ) : null}
          <input
            className="settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={bankPass}
            onChange={(e) => setBankPass(e.target.value)}
            placeholder={he.settings.leaveBlank}
          />
        </label>
        <label className="settings-check">
          <input type="checkbox" checked={invPortEn} onChange={(e) => setInvPortEn(e.target.checked)} />
          <span>{he.settings.enableLti}</span>
        </label>
      </section>

      <section className="card">
        <h2>{he.settings.max}</h2>
        <label className="settings-check">
          <input type="checkbox" checked={maxEn} onChange={(e) => setMaxEn(e.target.checked)} />
          <span>{he.settings.enabledFetch}</span>
        </label>
        <label className="settings-label">
          {he.settings.username}
          <input
            className="settings-input-wide"
            autoComplete="off"
            value={maxUser}
            onChange={(e) => setMaxUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          {he.settings.password}{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'max')?.credentials?.password_set ? (
            <span className="muted">{he.settings.passwordSet}</span>
          ) : null}
          <input
            className="settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={maxPass}
            onChange={(e) => setMaxPass(e.target.value)}
            placeholder={he.settings.leaveBlank}
          />
        </label>
      </section>

      <section className="card">
        <h2>{he.settings.isracard}</h2>
        <label className="settings-check">
          <input type="checkbox" checked={isrEn} onChange={(e) => setIsrEn(e.target.checked)} />
          <span>{he.settings.enabledFetch}</span>
        </label>
        <label className="settings-label">
          {he.settings.username}
          <input
            className="settings-input-wide"
            autoComplete="off"
            value={isrUser}
            onChange={(e) => setIsrUser(e.target.value)}
          />
        </label>
        <label className="settings-label">
          {he.settings.password}{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'isracard')?.credentials?.password_set ? (
            <span className="muted">{he.settings.passwordSet}</span>
          ) : null}
          <input
            className="settings-input-wide"
            type="password"
            autoComplete="new-password"
            value={isrPass}
            onChange={(e) => setIsrPass(e.target.value)}
            placeholder={he.settings.leaveBlank}
          />
        </label>
        <label className="settings-label">
          {he.settings.last6}{' '}
          {(cfg?.credit_cards ?? []).find((c) => c.id === 'isracard')?.credentials?.last6_set ? (
            <span className="muted">{he.settings.passwordSet}</span>
          ) : null}
          <input
            className="settings-input-narrow"
            autoComplete="off"
            value={isrLast6}
            onChange={(e) => setIsrLast6(e.target.value)}
            placeholder={he.settings.leaveBlank}
            maxLength={6}
          />
        </label>
      </section>

      <section className="card">
        <h2>{he.settings.sheets}</h2>
        <label className="settings-label">
          {he.settings.saPath}
          <input
            className="settings-input-wide"
            value={gPath}
            onChange={(e) => setGPath(e.target.value)}
            placeholder="e.g. data/private/your-project.json"
          />
        </label>
        <label className="settings-label">
          {he.settings.spreadsheetId}
          <input
            className="settings-input-wide"
            value={gSheet}
            onChange={(e) => setGSheet(e.target.value)}
          />
        </label>
      </section>

      <section className="card">
        <h2>{he.settings.dropRules}</h2>
        <p className="settings-hint">{he.settings.dropRulesHint}</p>
        <div className="settings-drop-rules-toolbar">
          <button type="button" className="secondary" onClick={addDropRuleRow}>
            {he.settings.addRule}
          </button>
          <button type="button" className="secondary" onClick={removeEmptyDropRuleRows}>
            {he.settings.removeEmpty}
          </button>
        </div>
        {dropRulesRows.length === 0 ? (
          <p className="settings-drop-rules-empty">{he.dashboard.empty}</p>
        ) : (
          <div className="settings-drop-rules-list">
            {dropRulesRows.map((row, idx) => (
              <div
                key={row.key}
                className={`settings-drop-rule-row${rowLooksIncomplete(row) ? ' settings-drop-rule-row--invalid' : ''}`}
              >
                <span className="settings-drop-rule-num">
                  {he.settings.ruleN} {idx + 1}
                </span>
                <div className="settings-drop-rule-fields">
                  <label className="settings-label">
                    {he.settings.column}
                    <input
                      className="settings-input-wide"
                      spellCheck={false}
                      value={row.column}
                      onChange={(e) => updateDropRuleRow(row.key, { column: e.target.value })}
                      placeholder="מקור עסקה"
                    />
                  </label>
                  <label className="settings-label">
                    {he.settings.value}
                    <input
                      className="settings-input-wide mono"
                      spellCheck={false}
                      value={row.value}
                      onChange={(e) => updateDropRuleRow(row.key, { value: e.target.value })}
                    />
                  </label>
                </div>
                <div className="settings-drop-rule-actions">
                  <button
                    type="button"
                    className="secondary"
                    title={he.settings.up}
                    disabled={idx === 0}
                    onClick={() => moveDropRuleRow(row.key, -1)}
                  >
                    {he.settings.up}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    title={he.settings.down}
                    disabled={idx === dropRulesRows.length - 1}
                    onClick={() => moveDropRuleRow(row.key, 1)}
                  >
                    {he.settings.down}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => removeDropRuleRow(row.key)}
                  >
                    {he.settings.remove}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="settings-actions settings-actions--inline">
          <button type="button" onClick={() => void saveDropRules()}>
            {he.settings.saveDropRules}
          </button>
        </div>
        <details className="settings-drop-rules-advanced">
          <summary>{he.settings.advancedJson}</summary>
          <div className="settings-drop-rules-advanced-inner">
            <p className="settings-hint" style={{ marginTop: 0 }}>
              {he.settings.advancedJsonHint}
            </p>
            <div className="settings-drop-rules-toolbar">
              <button
                type="button"
                className="secondary"
                onClick={() => syncAdvancedJsonFromRows(dropRulesRows)}
              >
                {he.settings.loadJson}
              </button>
              <button type="button" className="secondary" onClick={applyJsonToDropRulesTable}>
                {he.settings.applyJson}
              </button>
            </div>
            <textarea
              value={dropRulesAdvancedJson}
              onChange={(e) => setDropRulesAdvancedJson(e.target.value)}
              spellCheck={false}
              aria-label={he.settings.advancedJson}
            />
          </div>
        </details>
      </section>

      <CardPayerSection />

      <div className="settings-actions">
        <button type="button" onClick={() => void save()}>
          {he.settings.save}
        </button>
        <button type="button" className="secondary" onClick={() => void load()}>
          {he.settings.reload}
        </button>
        <button type="button" className="secondary" onClick={() => void importEnv()}>
          {he.settings.importEnv}
        </button>
      </div>
    </PageShell>
  );
}

type PayerRow = { last_four: string; payer: string };

function CardPayerSection() {
  const [rows, setRows] = useState<PayerRow[]>([]);
  const [known, setKnown] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  const loadPayers = useCallback(async () => {
    const r = await fetchJson<{ ok?: boolean; rows?: PayerRow[]; known_last_fours?: string[]; message?: string }>(
      '/api/books/card-payers',
    );
    if (!r.ok) {
      setError(typeof r.data.message === 'string' ? r.data.message : 'Request failed');
      return;
    }
    const mapped = new Map((r.data.rows ?? []).map((row) => [row.last_four, row.payer]));
    const digits = Array.from(new Set([...(r.data.known_last_fours ?? []), ...mapped.keys()]));
    setKnown(digits);
    setRows(digits.map((last_four) => ({ last_four, payer: mapped.get(last_four) ?? '' })));
  }, []);

  useEffect(() => {
    void loadPayers();
  }, [loadPayers]);

  async function savePayers() {
    setError('');
    setSaved(false);
    const r = await putJson('/api/books/card-payers', {
      rows: rows.filter((row) => row.last_four.trim() && row.payer.trim()),
    });
    if (!r.ok) {
      const data = r.data as { message?: string };
      setError(data.message || 'Request failed');
      return;
    }
    setSaved(true);
    await loadPayers();
  }

  return (
    <section className="card">
      <h2>{he.books.payers}</h2>
      <p className="hint">{he.books.payerHint}</p>
      {rows.length === 0 && known.length === 0 ? <p className="hint">{he.dashboard.empty}</p> : null}
      {rows.map((row, index) => (
        <div className="pipe-grid2" key={row.last_four} style={{ marginBottom: '0.5rem' }}>
          <label>
            {he.books.lastFour}
            <input value={row.last_four} readOnly />
          </label>
          <label>
            {he.books.payer}
            <input
              value={row.payer}
              onChange={(e) => {
                const next = rows.slice();
                next[index] = { ...row, payer: e.target.value };
                setRows(next);
              }}
            />
          </label>
        </div>
      ))}
      <button type="button" onClick={() => void savePayers()}>
        {he.books.savePayers}
      </button>
      {saved ? <p className="hint">{he.settings.saved}</p> : null}
      {error ? (
        <p className="banner banner--err" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
