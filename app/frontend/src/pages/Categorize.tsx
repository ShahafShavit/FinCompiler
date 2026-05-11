import { useCallback, useEffect, useMemo, useState } from 'react';

import { fetchJson } from '../lib/api';

import './Categorize.css';

const CAT = '/categorize';

type PayeeStoreMappingRow = { category: string; is_static: number | null };

type Pending = Record<string, unknown> & {
  kind: string;
  prompt_id?: string;
  store_name?: string;
  category?: string;
  dynamic_categories?: string[];
  all_categories?: string[];
  date?: unknown;
  expense?: unknown;
  income?: unknown;
  details?: unknown;
  digits?: unknown;
  ledger_id?: unknown;
  additional_detail?: unknown;
  notes?: unknown;
  statement_month?: unknown;
  ingested_at?: unknown;
  payee_store_mappings?: PayeeStoreMappingRow[];
  payee_mapping_kind?: string;
  payee_mapping_summary?: string;
  payee_distinct_category_count?: number;
};

type HistoryItem = {
  prompt_id: string;
  kind: string;
  store_name?: string;
  category?: string;
  response?: { category?: string; is_static?: number };
};

type CatApi = {
  pending?: Pending;
  history?: HistoryItem[];
  session_categories?: string[];
  open_count?: number;
  error?: string;
};

function normalizeApi(raw: unknown): CatApi {
  const data = raw as Record<string, unknown>;
  const empty: CatApi = {
    pending: { kind: 'idle' },
    history: [],
    session_categories: [],
  };
  if (data && data.pending !== undefined) {
    return {
      pending: (data.pending as Pending) || { kind: 'idle' },
      history: Array.isArray(data.history) ? (data.history as HistoryItem[]) : [],
      session_categories: Array.isArray(data.session_categories)
        ? (data.session_categories as string[])
        : [],
      open_count: typeof data.open_count === 'number' ? data.open_count : undefined,
      error: typeof data.error === 'string' ? data.error : undefined,
    };
  }
  if (data && data.kind) {
    return {
      ...empty,
      pending: data as Pending,
      open_count: typeof data.open_count === 'number' ? data.open_count : undefined,
      error: typeof data.error === 'string' ? data.error : undefined,
    };
  }
  return empty;
}

function mergeCategoryOptions(sessionCats: string[], pending: Pending | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (c: unknown) => {
    if (c === null || c === undefined || c === '') return;
    const t = String(c);
    if (seen.has(t)) return;
    seen.add(t);
    out.push(t);
  };
  (sessionCats || []).forEach(add);
  if (pending?.kind === 'fluid') {
    (pending.dynamic_categories as string[] | undefined)?.forEach(add);
    (pending.all_categories as string[] | undefined)?.forEach(add);
  }
  if (pending?.kind === 'new_store') {
    (pending.all_categories as string[] | undefined)?.forEach(add);
  }
  return out.sort((a, b) => a.localeCompare(b));
}

function esc(s: unknown): string {
  if (s === null || s === undefined) return '';
  return String(s);
}

function idSafe(s: string): string {
  return String(s).replace(/[^a-zA-Z0-9_-]/g, '_');
}

function readPickedCategory(pickName: string): string {
  const custom = (document.getElementById(`${pickName}-custom`) as HTMLInputElement | null)?.value.trim() ?? '';
  if (custom) return custom;
  const wrap = document.querySelector(`[data-cat-pick="${pickName}"]`);
  if (!wrap) return '';
  const sel = wrap.querySelector('.categorize-cat-badge--selected');
  return sel?.textContent?.trim() ?? '';
}

function readTxnNotes(textareaId: string): string {
  const raw = (document.getElementById(textareaId) as HTMLTextAreaElement | null)?.value ?? '';
  const t = raw.trim();
  return t.length > 8000 ? t.slice(0, 8000) : t;
}

function clearTxnNotesField(textareaId: string): void {
  const el = document.getElementById(textareaId) as HTMLTextAreaElement | null;
  if (el) el.value = '';
}

/** Read ``is_static`` (0 or 1) from mapping-type radio group ``name``, or ``null`` if none selected. */
function readMappingIsStatic(groupName: string): 0 | 1 | null {
  const el = document.querySelector(
    `input[type="radio"][name=${JSON.stringify(groupName)}]:checked`,
  ) as HTMLInputElement | null;
  if (!el?.value) return null;
  const v = parseInt(el.value, 10);
  return v === 0 ? 0 : 1;
}

function CategoryBadgePick({
  name,
  options,
  initialPick,
}: {
  name: string;
  options: string[];
  initialPick?: string;
}) {
  const sorted = useMemo(() => {
    const s = new Set<string>();
    options.forEach((x) => {
      const t = String(x).trim();
      if (t) s.add(t);
    });
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [options]);
  const [picked, setPicked] = useState<string | null>(null);
  const customId = `${name}-custom`;

  useEffect(() => {
    const t = (initialPick || '').trim();
    if (t && sorted.includes(t)) setPicked(t);
  }, [initialPick, sorted]);

  return (
    <div className="categorize-cat-pick" data-cat-pick={name}>
      <p className="categorize-cat-count">
        {sorted.length} {sorted.length === 1 ? 'category' : 'categories'} (sorted A–Z)
      </p>
      <div className="categorize-cat-badges" role="group" aria-label="Pick a category">
        {sorted.map((c) => (
          <button
            key={c}
            type="button"
            className={`categorize-cat-badge${picked === c ? ' categorize-cat-badge--selected' : ''}`}
            onClick={() => {
              setPicked(c);
              const el = document.getElementById(customId) as HTMLInputElement | null;
              if (el) el.value = '';
            }}
          >
            {c}
          </button>
        ))}
      </div>
      <label className="categorize-cat-custom-label" htmlFor={customId}>
        Or type a category
      </label>
      <input
        id={customId}
        className="categorize-cat-custom"
        type="text"
        autoComplete="off"
        placeholder="Name not in the list above"
        onChange={(e) => {
          if (e.target.value.trim()) setPicked(null);
        }}
      />
    </div>
  );
}

function MappingTypeBlock({ groupName }: { groupName: string }) {
  const staticId = `${groupName}-map-static`;
  const dynamicId = `${groupName}-map-dynamic`;
  return (
    <fieldset className="categorize-mapping-block categorize-mapping-radios" aria-required="true">
      <legend className="categorize-mapping-legend">Mapping type (required)</legend>
      <p className="hint categorize-mapping-help">
        <strong>Static</strong> — this payee name is tied to one category for good: new ledger rows with the same payee
        get this category automatically (after compile / auto-categorize), and other uncategorized rows for that payee
        can be forward-filled when you save. Use for subscriptions or merchants that should never change category.{' '}
        <br />
        <strong>Dynamic</strong> — only this row is labeled now; you can map the same payee to different categories on
        other rows. The app keeps several (store, category) pairs per payee and will keep asking when it is unsure.
      </p>
      <div className="categorize-mapping-radio-options" role="presentation">
        <label className="categorize-mapping-radio-tile" htmlFor={staticId}>
          <input type="radio" name={groupName} id={staticId} value="1" />
          <span className="categorize-mapping-radio-tile-body">
            <span className="categorize-mapping-radio-title">Static</span>
            <span className="categorize-mapping-radio-desc">Always use this category for this payee.</span>
          </span>
        </label>
        <label className="categorize-mapping-radio-tile" htmlFor={dynamicId}>
          <input type="radio" name={groupName} id={dynamicId} value="0" />
          <span className="categorize-mapping-radio-tile-body">
            <span className="categorize-mapping-radio-title">Dynamic</span>
            <span className="categorize-mapping-radio-desc">Pick per row; multiple categories allowed.</span>
          </span>
        </label>
      </div>
    </fieldset>
  );
}

function TxnNotesField({ id, defaultNotes }: { id: string; defaultNotes: string }) {
  return (
    <div className="categorize-notes-field">
      <label htmlFor={id}>Transaction notes</label>
      <p className="hint">Saved on this ledger row with the category (does not change the payee mapping).</p>
      <textarea id={id} className="categorize-notes-input" rows={3} defaultValue={defaultNotes} spellCheck={true} />
    </div>
  );
}

function summaryRow(label: string, value: unknown, opts?: { monoLtr?: boolean; muted?: boolean }) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const cls = opts?.monoLtr ? 'mono mono-ltr' : 'mono';
  const ddCls = [cls, opts?.muted ? 'categorize-summary-muted' : ''].filter(Boolean).join(' ');
  return (
    <>
      <dt>{label}</dt>
      <dd className={ddCls} {...(opts?.monoLtr ? { dir: 'ltr' as const } : {})}>
        {esc(value)}
      </dd>
    </>
  );
}

function payeeKindPillLabel(kind: string | undefined): string {
  switch (kind) {
    case 'static':
      return 'Static payee mapping';
    case 'dynamic':
      return 'Dynamic payee mapping';
    case 'mixed':
      return 'Mixed static & dynamic';
    case 'ambiguous':
      return 'Flag needs resolution';
    case 'unmapped':
      return 'Not in store table';
    default:
      return kind || '—';
  }
}

function PayeeStoreMappings({ data }: { data: Pending }) {
  const kind = data.payee_mapping_kind ?? 'unmapped';
  const summary = esc(data.payee_mapping_summary ?? '');
  const rows = (data.payee_store_mappings as PayeeStoreMappingRow[] | undefined) ?? [];
  const distinct = data.payee_distinct_category_count;
  const rowCount = rows.length;

  return (
    <div className={`categorize-payee-mappings categorize-payee-mappings--${kind}`}>
      <h3 className="categorize-payee-mappings-title">Payee in store table</h3>
      <p className="categorize-payee-summary">{summary}</p>
      <p className="categorize-payee-meta">
        <span className={`payee-kind-pill payee-kind-pill--${kind}`}>{payeeKindPillLabel(kind)}</span>
        {rowCount > 0 && (
          <>
            {' · '}
            <span>
              {rowCount} store row{rowCount === 1 ? '' : 's'}
            </span>
            {typeof distinct === 'number' && (
              <span>
                {' · '}
                {distinct} distinct {distinct === 1 ? 'category' : 'categories'}
              </span>
            )}
          </>
        )}
      </p>
      {rowCount > 0 && (
        <ul className="categorize-payee-map-list">
          {rows.map((r, i) => (
            <li key={`${r.category}-${i}`}>
              <span className="mono">{esc(r.category)}</span>
              <span className="categorize-payee-flag">
                {r.is_static === 1 ? ' — Static' : r.is_static === 0 ? ' — Dynamic' : ' — Uncertain flag'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TransactionDetails({ data }: { data: Pending }) {
  return (
    <div className="categorize-summary">
      <dl>
        {summaryRow('Date', data.date, { monoLtr: true })}
        {summaryRow('Expense (outflow)', data.expense, { monoLtr: true })}
        {summaryRow('Income (inflow)', data.income, { monoLtr: true })}
        {summaryRow('Description', data.details, { muted: true })}
        {summaryRow('Last 4 digits', data.digits, { monoLtr: true })}
        {summaryRow('Additional detail', data.additional_detail, { muted: true })}
        {summaryRow('Statement month', data.statement_month, { monoLtr: true })}
        {summaryRow('Ledger row ID', data.ledger_id, { monoLtr: true })}
        {summaryRow('Ingested at', data.ingested_at, { monoLtr: true, muted: true })}
      </dl>
    </div>
  );
}

export default function Categorize() {
  const [payload, setPayload] = useState<CatApi>({
    pending: { kind: 'idle' },
    history: [],
    session_categories: [],
  });
  const [status, setStatus] = useState('');

  const fetchNext = useCallback(async () => {
    try {
      const r = await fetch(`${CAT}/api/next`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      setPayload(normalizeApi(data));
    } catch (e) {
      setStatus(String(e));
    }
  }, []);

  useEffect(() => {
    void fetchNext();
  }, [fetchNext]);

  const postRevise = async (body: Record<string, unknown>, opts?: { clearNotesId?: string }) => {
    setStatus('Updating…');
    try {
      const r = await fetchJson(`${CAT}/api/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('Updated.');
      await fetchNext();
      if (opts?.clearNotesId) clearTxnNotesField(opts.clearNotesId);
    } catch (e) {
      setStatus(`Error: ${e}`);
    }
  };

  const submit = async (body: Record<string, unknown>, opts?: { clearNotesId?: string }) => {
    setStatus('Saving…');
    try {
      const r = await fetchJson(`${CAT}/api/respond`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('Saved.');
      await fetchNext();
      if (opts?.clearNotesId) clearTxnNotesField(opts.clearNotesId);
    } catch (e) {
      setStatus(`Error: ${e}`);
    }
  };

  const p = payload.pending || { kind: 'idle' };
  const sessionCats = payload.session_categories || [];
  const history = payload.history || [];

  const pendingOpts = useMemo(
    () => mergeCategoryOptions(sessionCats, p.kind !== 'idle' ? p : undefined),
    [sessionCats, p],
  );

  const queueMeta =
    typeof payload.open_count === 'number' ? (
      <p
        className={`categorize-queue-meta ${
          payload.open_count === 0 ? 'categorize-queue-meta--clear' : 'categorize-queue-meta--pending'
        }`}
      >
        {payload.open_count === 0
          ? 'Nothing waiting in the categorize queue.'
          : `${payload.open_count} transaction(s) still need a category.`}
      </p>
    ) : null;

  return (
    <div className="app-page app-page--narrow categorize-page">
      <h1>Manual categorization</h1>
      {queueMeta}

      {history.length > 0 && (
        <section className="categorize-card">
          <h2>This session (you can correct answers)</h2>
          {history.map((item) => {
            const hid = idSafe(item.prompt_id);
            const histFluidOpts = mergeCategoryOptions(
              sessionCats,
              { kind: 'fluid', dynamic_categories: [], all_categories: [] } as Pending,
            );
            return (
              <div key={item.prompt_id} className="hist-item">
                {item.kind === 'fluid' && (
                  <>
                    <p className="mono">{esc(item.store_name)}</p>
                    <CategoryBadgePick
                      key={`hb-${item.prompt_id}`}
                      name={`hist-fluid-${hid}`}
                      options={histFluidOpts}
                      initialPick={item.response?.category}
                    />
                    <TxnNotesField id={`hist-notes-fluid-${hid}`} defaultNotes="" />
                    <button
                      type="button"
                      className="small secondary"
                      onClick={() => {
                        const cat = readPickedCategory(`hist-fluid-${hid}`);
                        if (!cat) {
                          setStatus('Choose a category');
                          return;
                        }
                        void postRevise(
                          {
                            kind: 'fluid',
                            prompt_id: item.prompt_id,
                            category: cat,
                            notes: readTxnNotes(`hist-notes-fluid-${hid}`),
                          },
                          { clearNotesId: `hist-notes-fluid-${hid}` },
                        );
                      }}
                    >
                      Update category
                    </button>
                  </>
                )}
                {item.kind === 'new_store' && (
                  <>
                    <p className="mono">
                      {esc(item.store_name)} <span className="hint">(new payee)</span>
                    </p>
                    <CategoryBadgePick
                      key={`hn-${item.prompt_id}`}
                      name={`hist-new-${hid}`}
                      options={mergeCategoryOptions(sessionCats, {
                        kind: 'new_store',
                        all_categories: [],
                      } as Pending)}
                      initialPick={item.response?.category}
                    />
                    <MappingTypeBlock key={`map-hnst-${item.prompt_id}`} groupName={`hnst-${hid}`} />
                    <TxnNotesField id={`hist-notes-new-${hid}`} defaultNotes="" />
                    <button
                      type="button"
                      className="small secondary"
                      onClick={() => {
                        const cat = readPickedCategory(`hist-new-${hid}`);
                        if (!cat) {
                          setStatus('Choose a category');
                          return;
                        }
                        const is_static = readMappingIsStatic(`hnst-${hid}`);
                        if (is_static === null) {
                          setStatus('Choose Static or Dynamic mapping');
                          return;
                        }
                        void postRevise(
                          {
                            kind: 'new_store',
                            prompt_id: item.prompt_id,
                            category: cat,
                            is_static,
                            notes: readTxnNotes(`hist-notes-new-${hid}`),
                          },
                          { clearNotesId: `hist-notes-new-${hid}` },
                        );
                      }}
                    >
                      Update
                    </button>
                  </>
                )}
                {item.kind === 'resolve_static' && (
                  <>
                    <p>
                      <span className="mono">{esc(item.store_name)}</span> —{' '}
                      <span className="mono">{esc(item.category)}</span>
                    </p>
                    <TxnNotesField id={`hist-notes-rs-${hid}`} defaultNotes="" />
                    <div className="row2">
                      <button
                        type="button"
                        className="small secondary"
                        onClick={() =>
                          void postRevise(
                            {
                              kind: 'resolve_static',
                              prompt_id: item.prompt_id,
                              is_static: 0,
                              notes: readTxnNotes(`hist-notes-rs-${hid}`),
                            },
                            { clearNotesId: `hist-notes-rs-${hid}` },
                          )
                        }
                      >
                        Dynamic
                      </button>
                      <button
                        type="button"
                        className="small"
                        onClick={() =>
                          void postRevise(
                            {
                              kind: 'resolve_static',
                              prompt_id: item.prompt_id,
                              is_static: 1,
                              notes: readTxnNotes(`hist-notes-rs-${hid}`),
                            },
                            { clearNotesId: `hist-notes-rs-${hid}` },
                          )
                        }
                      >
                        Static
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </section>
      )}

      <section className="categorize-card">
        {payload.error && p.kind === 'idle' && (
          <p className="hint" role="alert">
            Queue: {esc(payload.error)}
          </p>
        )}
        {!p || p.kind === 'idle' ? (
          <>
            <p>No transaction is waiting for a category right now.</p>
            <p className="hint">When a new row appears, it will show here after you save or refresh.</p>
          </>
        ) : p.kind === 'fluid' ? (
          <>
            <p className="mono">{esc(p.store_name)}</p>
            <PayeeStoreMappings data={p} />
            <TransactionDetails data={p} />
            <label className="categorize-section-label">Category</label>
            <CategoryBadgePick key={`main-fluid-${String(p.prompt_id)}`} name="main-fluid" options={pendingOpts} />
            <TxnNotesField key={`txn-fluid-${p.prompt_id}`} id="notes-main-fluid" defaultNotes={esc(p.notes)} />
            <button
              type="button"
              onClick={() => {
                const cat = readPickedCategory('main-fluid');
                if (!cat) {
                  setStatus('Pick a category or type one');
                  return;
                }
                void submit(
                  {
                    kind: 'fluid',
                    prompt_id: p.prompt_id,
                    category: cat,
                    notes: readTxnNotes('notes-main-fluid'),
                  },
                  { clearNotesId: 'notes-main-fluid' },
                );
              }}
            >
              Save
            </button>
          </>
        ) : p.kind === 'resolve_static' ? (
          <>
            <p>
              Should category <strong className="mono">{esc(p.category)}</strong> for payee{' '}
              <span className="mono">{esc(p.store_name)}</span> be a fixed (static) mapping?
            </p>
            <TransactionDetails data={p} />
            <p className="hint categorize-mapping-help">
              You are deciding whether the <strong>existing</strong> mapping for this payee and category is{' '}
              <strong>Static</strong> (always use this category for this payee going forward, with forward-fill) or{' '}
              <strong>Dynamic</strong> (this payee may keep multiple categories; you will pick per row when needed).
            </p>
            <TxnNotesField key={`txn-resolve-${p.prompt_id}`} id="notes-main-resolve" defaultNotes={esc(p.notes)} />
            <div className="row2">
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  void submit(
                    {
                      kind: 'resolve_static',
                      prompt_id: p.prompt_id,
                      is_static: 0,
                      notes: readTxnNotes('notes-main-resolve'),
                    },
                    { clearNotesId: 'notes-main-resolve' },
                  )
                }
              >
                Dynamic (0)
              </button>
              <button
                type="button"
                onClick={() =>
                  void submit(
                    {
                      kind: 'resolve_static',
                      prompt_id: p.prompt_id,
                      is_static: 1,
                      notes: readTxnNotes('notes-main-resolve'),
                    },
                    { clearNotesId: 'notes-main-resolve' },
                  )
                }
              >
                Static (1)
              </button>
            </div>
          </>
        ) : p.kind === 'new_store' ? (
          <>
            <p>
              New payee: <span className="mono">{esc(p.store_name)}</span>
            </p>
            <PayeeStoreMappings data={p} />
            <TransactionDetails data={p} />
            <label className="categorize-section-label">Category</label>
            <CategoryBadgePick key={`main-new-${String(p.prompt_id)}`} name="main-new" options={pendingOpts} />
            <MappingTypeBlock key={`map-nst-${p.prompt_id}`} groupName="nst-main" />
            <TxnNotesField key={`txn-new-${p.prompt_id}`} id="notes-main-new" defaultNotes={esc(p.notes)} />
            <button
              type="button"
              onClick={() => {
                const cat = readPickedCategory('main-new');
                if (!cat) {
                  setStatus('Pick a category or type one');
                  return;
                }
                const is_static = readMappingIsStatic('nst-main');
                if (is_static === null) {
                  setStatus('Choose Static or Dynamic mapping');
                  return;
                }
                void submit(
                  {
                    kind: 'new_store',
                    prompt_id: p.prompt_id,
                    category: cat,
                    is_static,
                    notes: readTxnNotes('notes-main-new'),
                  },
                  { clearNotesId: 'notes-main-new' },
                );
              }}
            >
              Save
            </button>
          </>
        ) : (
          <p>Unknown prompt state</p>
        )}
      </section>

      <p className="categorize-status">{status}</p>
    </div>
  );
}
