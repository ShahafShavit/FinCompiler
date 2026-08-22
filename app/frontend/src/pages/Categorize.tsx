import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import PageShell from '../components/PageShell';
import CashRegister from '../components/CashRegister';
import { fetchJson, postJson } from '../lib/api';
import { he } from '../lib/he';
import { useInvalidateCategorizeSummary } from '../lib/useCategorizeSummary';

import './Categorize.css';
import './Cash.css';

const CAT = '/categorize';

type PayeeStoreMappingRow = { category: string; is_static: number | null };

type CategorySuggestionRow = { category: string; confidence_pct: number };

type Pending = Record<string, unknown> & {
  kind: string;
  prompt_id?: string;
  store_name?: string;
  category?: string;
  dynamic_categories?: string[];
  all_categories?: string[];
  category_suggestions?: CategorySuggestionRow[];
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

type CategoryColumnsPayload = {
  columns: { top_name: string; sub_categories: string[] }[];
  unassigned: string[];
};

type CatApi = {
  pending?: Pending;
  history?: HistoryItem[];
  session_categories?: string[];
  open_count?: number;
  error?: string;
  category_columns?: CategoryColumnsPayload;
};

function pickCategoryColumns(data: Record<string, unknown>): CategoryColumnsPayload | undefined {
  const raw = data.category_columns;
  if (!raw || typeof raw !== 'object') return undefined;
  const obj = raw as Record<string, unknown>;
  const cols = obj.columns;
  const un = obj.unassigned;
  if (!Array.isArray(cols)) return undefined;
  const columns = cols
    .filter((c): c is Record<string, unknown> => Boolean(c && typeof c === 'object'))
    .map((c) => ({
      top_name: String(c.top_name ?? ''),
      sub_categories: Array.isArray(c.sub_categories)
        ? (c.sub_categories as unknown[]).map((x) => String(x))
        : [],
    }))
    .filter((c) => c.top_name.trim());
  const unassigned = Array.isArray(un) ? (un as unknown[]).map((x) => String(x)) : [];
  return { columns, unassigned };
}

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
      category_columns: pickCategoryColumns(data),
    };
  }
  if (data && data.kind) {
    return {
      ...empty,
      pending: data as Pending,
      open_count: typeof data.open_count === 'number' ? data.open_count : undefined,
      error: typeof data.error === 'string' ? data.error : undefined,
      category_columns: pickCategoryColumns(data),
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

function resolveCategory(picked: string | null, custom: string): string {
  const c = custom.trim();
  if (c) return c;
  return (picked ?? '').trim();
}

function clipNotes(notes: string): string {
  const t = notes.trim();
  return t.length > 8000 ? t.slice(0, 8000) : t;
}

function scrollCategorizePageToTop(): void {
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function DiscardTrashIcon() {
  const stroke = {
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  return (
    <svg
      className="categorize-discard-btn__icon"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path d="M3 6h18" {...stroke} />
      <path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2" {...stroke} />
      <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6" {...stroke} />
      <path d="M10 11v6" {...stroke} />
      <path d="M14 11v6" {...stroke} />
    </svg>
  );
}

function DiscardTxnButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="categorize-discard-btn categorize-discard-btn--footer"
      title={he.categorize.discard}
      onClick={onClick}
    >
      <DiscardTrashIcon />
      <span>{he.categorize.discard}</span>
    </button>
  );
}

function categoryBadgeClassName(
  category: string,
  picked: string | null,
  hoveredCategory: string | null | undefined,
  extra = '',
): string {
  const isSelected = picked === category;
  const isHovered = !isSelected && hoveredCategory === category;
  return `categorize-cat-badge${extra}${isSelected ? ' categorize-cat-badge--selected' : ''}${
    isHovered ? ' categorize-cat-badge--hovered' : ''
  }`;
}

function CategorySuggestions({
  suggestions,
  selectedCategory,
  hoveredCategory,
  onPick,
  onHoverCategory,
}: {
  suggestions: CategorySuggestionRow[];
  selectedCategory?: string | null;
  hoveredCategory?: string | null;
  onPick: (category: string) => void;
  onHoverCategory?: (category: string | null) => void;
}) {
  if (!suggestions.length) return null;
  return (
    <section className="categorize-suggestions" aria-label={he.categorize.suggestions}>
      <p className="categorize-suggestions-title">{he.categorize.suggestions}</p>
      <p className="hint categorize-suggestions-hint">{he.categorize.suggestionsHint}</p>
      <div className="categorize-suggestions-badges" role="group" aria-label={he.categorize.suggestions}>
        {suggestions.map((s) => {
          const isSelected = selectedCategory === s.category;
          return (
            <button
              key={`${s.category}-${s.confidence_pct}`}
              type="button"
              className={categoryBadgeClassName(
                s.category,
                selectedCategory ?? null,
                hoveredCategory,
                ' categorize-cat-badge--suggested',
              )}
              aria-pressed={isSelected}
              onMouseEnter={() => onHoverCategory?.(s.category)}
              onClick={() => onPick(s.category)}
            >
              {s.category}
              <span className="categorize-suggestion-pct">{Math.round(s.confidence_pct)}%</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function CategoryBadgePick({
  options,
  picked,
  custom,
  onPick,
  onCustomChange,
  categoryColumns,
  hoveredCategory,
  onHoverCategory,
}: {
  options: string[];
  picked: string | null;
  custom: string;
  onPick: (category: string) => void;
  onCustomChange: (value: string) => void;
  categoryColumns?: CategoryColumnsPayload | null;
  hoveredCategory?: string | null;
  onHoverCategory?: (category: string | null) => void;
}) {
  const sorted = useMemo(() => {
    const s = new Set<string>();
    options.forEach((x) => {
      const t = String(x).trim();
      if (t) s.add(t);
    });
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [options]);

  const useGrouped = Boolean(categoryColumns?.columns && categoryColumns.columns.length > 0);

  const grouped = useMemo(() => {
    if (!useGrouped || !categoryColumns) return null;
    const optSet = new Set(sorted);
    const cols = categoryColumns.columns.map((c) => ({
      top_name: c.top_name,
      subs: c.sub_categories.filter((s) => optSet.has(s)),
    }));
    const fromServerU = categoryColumns.unassigned.filter((s) => optSet.has(s));
    const assigned = new Set<string>();
    cols.forEach((c) => c.subs.forEach((s) => assigned.add(s)));
    categoryColumns.unassigned.forEach((s) => {
      if (optSet.has(s)) assigned.add(s);
    });
    const orphans = sorted.filter((s) => !assigned.has(s));
    const unBlock = [...new Set([...fromServerU, ...orphans])].sort((a, b) => a.localeCompare(b));
    return { cols, unBlock };
  }, [useGrouped, categoryColumns, sorted]);

  const effectivePicked = custom.trim() ? null : picked;

  return (
    <div className="categorize-cat-pick">
      <p className="categorize-cat-count">
        {sorted.length} {sorted.length === 1 ? 'category' : 'categories'}
        {useGrouped && grouped ? ' (grouped)' : ' (sorted A–Z)'}
      </p>
      {useGrouped && grouped ? (
        <div className="categorize-cat-groups" role="group" aria-label={he.categorize.pickCategory}>
          {grouped.cols.map((col) =>
            col.subs.length === 0 ? null : (
              <div key={col.top_name} className="categorize-cat-group">
                <p className="categorize-cat-group-title">{col.top_name}</p>
                <div className="categorize-cat-badges">
                  {col.subs.map((c) => (
                    <button
                      key={c}
                      type="button"
                      className={categoryBadgeClassName(c, effectivePicked, hoveredCategory)}
                      onMouseEnter={() => onHoverCategory?.(c)}
                      onClick={() => onPick(c)}
                    >
                      {c}
                    </button>
                  ))}
                </div>
              </div>
            ),
          )}
          {grouped.unBlock.length > 0 ? (
            <div className="categorize-cat-group">
              <p className="categorize-cat-group-title">Unassigned</p>
              <div className="categorize-cat-badges">
                {grouped.unBlock.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={categoryBadgeClassName(c, effectivePicked, hoveredCategory)}
                    onMouseEnter={() => onHoverCategory?.(c)}
                    onClick={() => onPick(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="categorize-cat-badges" role="group" aria-label={he.categorize.pickCategory}>
          {sorted.map((c) => (
            <button
              key={c}
              type="button"
              className={categoryBadgeClassName(c, effectivePicked, hoveredCategory)}
              onMouseEnter={() => onHoverCategory?.(c)}
              onClick={() => onPick(c)}
            >
              {c}
            </button>
          ))}
        </div>
      )}
      <label className="categorize-cat-custom-label">{he.categorize.customCategory}</label>
      <input
        className="categorize-cat-custom"
        type="text"
        autoComplete="off"
        placeholder={he.categorize.customCategory}
        value={custom}
        onChange={(e) => onCustomChange(e.target.value)}
      />
    </div>
  );
}

function MappingTypeBlock({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: 0 | 1 | null;
  onChange: (v: 0 | 1) => void;
}) {
  const staticId = `${idPrefix}-map-static`;
  const dynamicId = `${idPrefix}-map-dynamic`;
  return (
    <fieldset className="categorize-mapping-block categorize-mapping-radios" aria-required="true">
      <legend className="categorize-mapping-legend">Mapping type (required)</legend>
      <p className="hint categorize-mapping-help">
        <strong>{he.categorize.staticMapping}</strong> — this payee name is tied to one category for good: new ledger
        rows with the same payee get this category automatically (after compile / auto-categorize), and other
        uncategorized rows for that payee can be forward-filled when you save. Use for subscriptions or merchants that
        should never change category. <br />
        <strong>{he.categorize.dynamicMapping}</strong> — only this row is labeled now; you can map the same payee to
        different categories on other rows. The app keeps several (store, category) pairs per payee and will keep asking
        when it is unsure.
      </p>
      <div className="categorize-mapping-radio-options" role="presentation">
        <label className="categorize-mapping-radio-tile" htmlFor={staticId}>
          <input
            type="radio"
            name={idPrefix}
            id={staticId}
            value="1"
            checked={value === 1}
            onChange={() => onChange(1)}
          />
          <span className="categorize-mapping-radio-tile-body">
            <span className="categorize-mapping-radio-title">{he.categorize.staticMapping}</span>
            <span className="categorize-mapping-radio-desc">Always use this category for this payee.</span>
          </span>
        </label>
        <label className="categorize-mapping-radio-tile" htmlFor={dynamicId}>
          <input
            type="radio"
            name={idPrefix}
            id={dynamicId}
            value="0"
            checked={value === 0}
            onChange={() => onChange(0)}
          />
          <span className="categorize-mapping-radio-tile-body">
            <span className="categorize-mapping-radio-title">{he.categorize.dynamicMapping}</span>
            <span className="categorize-mapping-radio-desc">Pick per row; multiple categories allowed.</span>
          </span>
        </label>
      </div>
    </fieldset>
  );
}

function TxnNotesField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="categorize-notes-field">
      <label htmlFor={id}>{he.categorize.notes}</label>
      <p className="hint">Saved on this ledger row with the category (does not change the payee mapping).</p>
      <textarea
        id={id}
        className="categorize-notes-input"
        rows={3}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={true}
      />
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
                {r.is_static === 1
                  ? ` — ${he.categorize.staticMapping}`
                  : r.is_static === 0
                    ? ` — ${he.categorize.dynamicMapping}`
                    : ' — Uncertain flag'}
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

type HistoryHandlers = {
  sessionCats: string[];
  categoryColumns?: CategoryColumnsPayload;
  onRevise: (body: Record<string, unknown>) => void;
  onStatus: (msg: string) => void;
};

function HistoryFluidItem({ item, sessionCats, categoryColumns, onRevise, onStatus }: HistoryHandlers & { item: HistoryItem }) {
  const [category, setCategory] = useState<string | null>(item.response?.category ?? null);
  const [custom, setCustom] = useState('');
  const [notes, setNotes] = useState('');
  const opts = useMemo(
    () =>
      mergeCategoryOptions(sessionCats, {
        kind: 'fluid',
        dynamic_categories: [],
        all_categories: [],
      } as Pending),
    [sessionCats],
  );

  const pick = (c: string) => {
    setCategory(c);
    setCustom('');
  };

  return (
    <div className="hist-item">
      <p className="mono">{esc(item.store_name)}</p>
      <CategoryBadgePick
        options={opts}
        picked={category}
        custom={custom}
        onPick={pick}
        onCustomChange={(v) => {
          setCustom(v);
          if (v.trim()) setCategory(null);
        }}
        categoryColumns={categoryColumns}
      />
      <TxnNotesField id={`hist-notes-fluid-${item.prompt_id}`} value={notes} onChange={setNotes} />
      <button
        type="button"
        className="small secondary"
        onClick={() => {
          const cat = resolveCategory(category, custom);
          if (!cat) {
            onStatus(he.categorize.pickCategory);
            return;
          }
          onRevise({
            kind: 'fluid',
            prompt_id: item.prompt_id,
            category: cat,
            notes: clipNotes(notes),
          });
          setNotes('');
        }}
      >
        Update category
      </button>
    </div>
  );
}

function HistoryNewStoreItem({
  item,
  sessionCats,
  categoryColumns,
  onRevise,
  onStatus,
}: HistoryHandlers & { item: HistoryItem }) {
  const initialStatic =
    item.response?.is_static === 0 || item.response?.is_static === 1
      ? (item.response.is_static as 0 | 1)
      : null;
  const [category, setCategory] = useState<string | null>(item.response?.category ?? null);
  const [custom, setCustom] = useState('');
  const [notes, setNotes] = useState('');
  const [isStatic, setIsStatic] = useState<0 | 1 | null>(initialStatic);
  const opts = useMemo(
    () =>
      mergeCategoryOptions(sessionCats, {
        kind: 'new_store',
        all_categories: [],
      } as Pending),
    [sessionCats],
  );

  const pick = (c: string) => {
    setCategory(c);
    setCustom('');
  };

  return (
    <div className="hist-item">
      <p className="mono">
        {esc(item.store_name)} <span className="hint">(new payee)</span>
      </p>
      <CategoryBadgePick
        options={opts}
        picked={category}
        custom={custom}
        onPick={pick}
        onCustomChange={(v) => {
          setCustom(v);
          if (v.trim()) setCategory(null);
        }}
        categoryColumns={categoryColumns}
      />
      <MappingTypeBlock idPrefix={`hnst-${item.prompt_id}`} value={isStatic} onChange={setIsStatic} />
      <TxnNotesField id={`hist-notes-new-${item.prompt_id}`} value={notes} onChange={setNotes} />
      <button
        type="button"
        className="small secondary"
        onClick={() => {
          const cat = resolveCategory(category, custom);
          if (!cat) {
            onStatus(he.categorize.pickCategory);
            return;
          }
          if (isStatic === null) {
            onStatus(`${he.categorize.staticMapping} / ${he.categorize.dynamicMapping}`);
            return;
          }
          onRevise({
            kind: 'new_store',
            prompt_id: item.prompt_id,
            category: cat,
            is_static: isStatic,
            notes: clipNotes(notes),
          });
          setNotes('');
        }}
      >
        Update
      </button>
    </div>
  );
}

function HistoryResolveStaticItem({
  item,
  onRevise,
}: {
  item: HistoryItem;
  onRevise: (body: Record<string, unknown>) => void;
}) {
  const [notes, setNotes] = useState('');

  return (
    <div className="hist-item">
      <p>
        <span className="mono">{esc(item.store_name)}</span> — <span className="mono">{esc(item.category)}</span>
      </p>
      <TxnNotesField id={`hist-notes-rs-${item.prompt_id}`} value={notes} onChange={setNotes} />
      <div className="row2">
        <button
          type="button"
          className="small secondary"
          onClick={() => {
            onRevise({
              kind: 'resolve_static',
              prompt_id: item.prompt_id,
              is_static: 0,
              notes: clipNotes(notes),
            });
            setNotes('');
          }}
        >
          {he.categorize.dynamicMapping}
        </button>
        <button
          type="button"
          className="small"
          onClick={() => {
            onRevise({
              kind: 'resolve_static',
              prompt_id: item.prompt_id,
              is_static: 1,
              notes: clipNotes(notes),
            });
            setNotes('');
          }}
        >
          {he.categorize.staticMapping}
        </button>
      </div>
    </div>
  );
}

export default function Categorize() {
  const invalidateSummary = useInvalidateCategorizeSummary();
  const historySectionRef = useRef<HTMLElement | null>(null);
  const [payload, setPayload] = useState<CatApi>({
    pending: { kind: 'idle' },
    history: [],
    session_categories: [],
  });
  const [status, setStatus] = useState('');
  const [hoveredCategory, setHoveredCategory] = useState<string | null>(null);

  const [mainCategory, setMainCategory] = useState<string | null>(null);
  const [mainCustom, setMainCustom] = useState('');
  const [mainNotes, setMainNotes] = useState('');
  const [mainIsStatic, setMainIsStatic] = useState<0 | 1 | null>(null);

  const fetchNext = useCallback(async () => {
    try {
      const r = await fetch(`${CAT}/api/next`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      setPayload(normalizeApi(data));
      invalidateSummary();
    } catch (e) {
      setStatus(String(e));
    }
  }, [invalidateSummary]);

  useEffect(() => {
    void fetchNext();
  }, [fetchNext]);

  const postRevise = async (body: Record<string, unknown>) => {
    setStatus('Updating…');
    try {
      const r = await fetchJson(`${CAT}/api/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('Updated.');
      invalidateSummary();
      await fetchNext();
    } catch (e) {
      setStatus(`Error: ${e}`);
    }
  };

  const submit = useCallback(
    async (body: Record<string, unknown>) => {
      setStatus('Saving…');
      scrollCategorizePageToTop();
      try {
        const r = await fetchJson(`${CAT}/api/respond`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
        setHoveredCategory(null);
        setPayload((prev) => ({ ...prev, pending: { kind: 'idle' } }));
        setStatus('Saved.');
        invalidateSummary();
        await fetchNext();
      } catch (e) {
        setStatus(`Error: ${e}`);
      }
    },
    [fetchNext, invalidateSummary],
  );

  const postDiscard = async (promptId: string) => {
    if (!window.confirm(he.categorize.discardConfirm)) return;
    setStatus('Discarding…');
    try {
      const r = await postJson<{ ok?: boolean; error?: string }>(`${CAT}/api/discard`, {
        prompt_id: promptId,
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('Discarded.');
      invalidateSummary();
      await fetchNext();
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

  const categoryColumns = payload.category_columns;
  const pendingPromptId = p.kind !== 'idle' ? String(p.prompt_id ?? '') : '';

  useEffect(() => {
    setMainCategory(null);
    setMainCustom('');
    setMainNotes(p.kind !== 'idle' ? esc(p.notes) : '');
    setMainIsStatic(null);
    setHoveredCategory(null);
    // Reset form when the pending prompt changes; notes seed from that prompt.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reset on prompt id
  }, [pendingPromptId]);

  const pickMain = useCallback((category: string) => {
    setMainCategory(category);
    setMainCustom('');
  }, []);

  const onMainCustomChange = (value: string) => {
    setMainCustom(value);
    if (value.trim()) setMainCategory(null);
  };

  const trySaveMain = useCallback(() => {
    if (!p || p.kind === 'idle') return;
    if (p.kind === 'fluid') {
      const cat = resolveCategory(mainCategory, mainCustom);
      if (!cat) {
        setStatus(he.categorize.pickCategory);
        return;
      }
      void submit({
        kind: 'fluid',
        prompt_id: p.prompt_id,
        category: cat,
        notes: clipNotes(mainNotes),
      });
      return;
    }
    if (p.kind === 'new_store') {
      const cat = resolveCategory(mainCategory, mainCustom);
      if (!cat) {
        setStatus(he.categorize.pickCategory);
        return;
      }
      if (mainIsStatic === null) {
        setStatus(`${he.categorize.staticMapping} / ${he.categorize.dynamicMapping}`);
        return;
      }
      void submit({
        kind: 'new_store',
        prompt_id: p.prompt_id,
        category: cat,
        is_static: mainIsStatic,
        notes: clipNotes(mainNotes),
      });
      return;
    }
    if (p.kind === 'resolve_static') {
      // Require an explicit static/dynamic click; Enter alone is ambiguous.
      setStatus(`${he.categorize.staticMapping} / ${he.categorize.dynamicMapping}`);
    }
  }, [p, mainCategory, mainCustom, mainNotes, mainIsStatic, submit]);

  const suggestions = Array.isArray(p.category_suggestions) ? p.category_suggestions : [];
  const selectedForSuggestions = mainCustom.trim() ? null : mainCategory;

  useEffect(() => {
    const isTypingTarget = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
      return el.isContentEditable;
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isTypingTarget(e.target)) {
          (e.target as HTMLElement).blur();
          e.preventDefault();
        }
        return;
      }

      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z') && !e.shiftKey) {
        if (isTypingTarget(e.target)) return;
        if (history.length === 0) return;
        e.preventDefault();
        historySectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        setStatus(he.categorize.session);
        return;
      }

      const ctrlEnter = (e.ctrlKey || e.metaKey) && e.key === 'Enter';
      if (ctrlEnter) {
        e.preventDefault();
        trySaveMain();
        return;
      }

      if (isTypingTarget(e.target)) return;

      if (e.key === 'Enter') {
        e.preventDefault();
        trySaveMain();
        return;
      }

      if (e.key >= '1' && e.key <= '9' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const idx = Number(e.key) - 1;
        if (idx < suggestions.length) {
          e.preventDefault();
          pickMain(suggestions[idx].category);
        }
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [trySaveMain, suggestions, pickMain, history.length]);

  const queueMeta =
    typeof payload.open_count === 'number' ? (
      <p
        className={`categorize-queue-meta ${
          payload.open_count === 0 ? 'categorize-queue-meta--clear' : 'categorize-queue-meta--pending'
        }`}
      >
        {payload.open_count === 0
          ? he.categorize.queueClear
          : `${payload.open_count} · ${he.categorize.queuePending}`}
      </p>
    ) : null;

  const historyHandlers: HistoryHandlers = {
    sessionCats,
    categoryColumns,
    onRevise: (body) => void postRevise(body),
    onStatus: setStatus,
  };

  return (
    <PageShell title={he.categorize.title} sub={he.categorize.sub} width="narrow" className="categorize-page">
      {queueMeta}
      <p className="hint categorize-shortcuts-hint">{he.categorize.shortcutsHint}</p>

      <section className="categorize-card categorize-card--hero">
        {payload.error && p.kind === 'idle' && (
          <p className="hint" role="alert">
            Queue: {esc(payload.error)}
          </p>
        )}
        {!p || p.kind === 'idle' ? (
          <>
            <p>{he.categorize.queueClear}</p>
            <p className="hint">{he.categorize.cashAfterQueueHint}</p>
            <h2 className="categorize-cash-heading">{he.categorize.cashAfterQueue}</h2>
            <CashRegister compact />
          </>
        ) : p.kind === 'fluid' ? (
          <>
            <p className="mono">{esc(p.store_name)}</p>
            <PayeeStoreMappings data={p} />
            <TransactionDetails data={p} />
            <label className="categorize-section-label">{he.categorize.pickCategory}</label>
            <div className="categorize-category-pick-zone" onMouseLeave={() => setHoveredCategory(null)}>
              <CategorySuggestions
                suggestions={suggestions}
                selectedCategory={selectedForSuggestions}
                hoveredCategory={hoveredCategory}
                onPick={pickMain}
                onHoverCategory={setHoveredCategory}
              />
              <CategoryBadgePick
                options={pendingOpts}
                picked={mainCategory}
                custom={mainCustom}
                onPick={pickMain}
                onCustomChange={onMainCustomChange}
                categoryColumns={categoryColumns}
                hoveredCategory={hoveredCategory}
                onHoverCategory={setHoveredCategory}
              />
            </div>
            <TxnNotesField id="notes-main-fluid" value={mainNotes} onChange={setMainNotes} />
            <div className="categorize-action-footer">
              <div className="categorize-action-footer__row">
                <button
                  type="button"
                  className="categorize-btn-save"
                  onClick={() => trySaveMain()}
                >
                  {he.categorize.save}
                </button>
                <DiscardTxnButton onClick={() => void postDiscard(String(p.prompt_id))} />
              </div>
              <p className="hint categorize-discard-hint categorize-action-footer__hint">
                Discard excludes this row from totals and categorize, and adds a compile-time drop rule for this exact
                payee/source string (מקור עסקה) so future imports with the same value are removed before the ledger.
              </p>
            </div>
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
              <strong>{he.categorize.staticMapping}</strong> (always use this category for this payee going forward, with
              forward-fill) or <strong>{he.categorize.dynamicMapping}</strong> (this payee may keep multiple
              categories; you will pick per row when needed).
            </p>
            <TxnNotesField id="notes-main-resolve" value={mainNotes} onChange={setMainNotes} />
            <div className="row2">
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  void submit({
                    kind: 'resolve_static',
                    prompt_id: p.prompt_id,
                    is_static: 0,
                    notes: clipNotes(mainNotes),
                  })
                }
              >
                {he.categorize.dynamicMapping}
              </button>
              <button
                type="button"
                onClick={() =>
                  void submit({
                    kind: 'resolve_static',
                    prompt_id: p.prompt_id,
                    is_static: 1,
                    notes: clipNotes(mainNotes),
                  })
                }
              >
                {he.categorize.staticMapping}
              </button>
            </div>
            <div className="categorize-action-footer">
              <div className="categorize-action-footer__row categorize-action-footer__row--only-danger">
                <DiscardTxnButton onClick={() => void postDiscard(String(p.prompt_id))} />
              </div>
              <p className="hint categorize-discard-hint categorize-action-footer__hint">
                Discard excludes this row and adds a drop rule for this payee/source (מקור עסקה) on future compiles.
              </p>
            </div>
          </>
        ) : p.kind === 'new_store' ? (
          <>
            <p>
              New payee: <span className="mono">{esc(p.store_name)}</span>
            </p>
            <PayeeStoreMappings data={p} />
            <TransactionDetails data={p} />
            <label className="categorize-section-label">{he.categorize.pickCategory}</label>
            <div className="categorize-category-pick-zone" onMouseLeave={() => setHoveredCategory(null)}>
              <CategorySuggestions
                suggestions={suggestions}
                selectedCategory={selectedForSuggestions}
                hoveredCategory={hoveredCategory}
                onPick={pickMain}
                onHoverCategory={setHoveredCategory}
              />
              <CategoryBadgePick
                options={pendingOpts}
                picked={mainCategory}
                custom={mainCustom}
                onPick={pickMain}
                onCustomChange={onMainCustomChange}
                categoryColumns={categoryColumns}
                hoveredCategory={hoveredCategory}
                onHoverCategory={setHoveredCategory}
              />
            </div>
            <MappingTypeBlock idPrefix="nst-main" value={mainIsStatic} onChange={setMainIsStatic} />
            <TxnNotesField id="notes-main-new" value={mainNotes} onChange={setMainNotes} />
            <div className="categorize-action-footer">
              <div className="categorize-action-footer__row">
                <button
                  type="button"
                  className="categorize-btn-save"
                  onClick={() => trySaveMain()}
                >
                  {he.categorize.save}
                </button>
                <DiscardTxnButton onClick={() => void postDiscard(String(p.prompt_id))} />
              </div>
              <p className="hint categorize-discard-hint categorize-action-footer__hint">
                Discard excludes this row from totals and categorize, and adds a compile-time drop rule for this exact
                payee/source string (מקור עסקה) on future imports.
              </p>
            </div>
          </>
        ) : (
          <p>Unknown prompt state</p>
        )}
      </section>

      {history.length > 0 && (
        <section
          className="categorize-card categorize-card--history"
          ref={historySectionRef}
        >
          <h2>{he.categorize.session}</h2>
          {history.map((item) => {
            if (item.kind === 'fluid') {
              return <HistoryFluidItem key={item.prompt_id} item={item} {...historyHandlers} />;
            }
            if (item.kind === 'new_store') {
              return <HistoryNewStoreItem key={item.prompt_id} item={item} {...historyHandlers} />;
            }
            if (item.kind === 'resolve_static') {
              return (
                <HistoryResolveStaticItem
                  key={item.prompt_id}
                  item={item}
                  onRevise={(body) => void postRevise(body)}
                />
              );
            }
            return null;
          })}
        </section>
      )}

      <p className="categorize-status">{status}</p>
    </PageShell>
  );
}
