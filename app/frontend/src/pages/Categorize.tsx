import { useCallback, useEffect, useMemo, useState } from 'react';

import { fetchJson } from '../lib/api';

import './Categorize.css';

const CAT = '/categorize';

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
};

function normalizeApi(raw: unknown): CatApi {
  const data = raw as Record<string, unknown>;
  if (data && data.pending !== undefined) return data as CatApi;
  if (data && data.kind) {
    return { pending: data as Pending, history: [], session_categories: [] };
  }
  return { pending: { kind: 'idle' }, history: [], session_categories: [] };
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
  return out;
}

function esc(s: unknown): string {
  if (s === null || s === undefined) return '';
  return String(s);
}

function CategoryCombo({
  inputId,
  selected,
  options,
}: {
  inputId: string;
  selected: string;
  options: string[];
}) {
  const dlId = `${inputId}-dl`;
  return (
    <div className="combo-cat-wrap">
      <input
        type="text"
        className="combo-cat"
        id={inputId}
        list={dlId}
        autoComplete="off"
        placeholder="בחר מהרשימה או הקלד"
        defaultValue={selected}
        key={`${inputId}-${selected}`}
      />
      <span className="combo-cat-affordance" aria-hidden title="ניתן לבחור מהרשימה או להקליד">
        ▼
      </span>
      <datalist id={dlId}>
        {options.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </div>
  );
}

function TransactionDetails({ data }: { data: Pending }) {
  return (
    <>
      <label>תאריך</label>
      <div className="mono mono-ltr" dir="ltr">
        {esc(data.date)}
      </div>
      <label>בחובה / בזכות</label>
      <div className="mono mono-ltr" dir="ltr">
        {esc(data.expense)} / {esc(data.income)}
      </div>
      {data.details != null && String(data.details) !== '' && (
        <>
          <label>תאור מורחב</label>
          <div className="mono">{esc(data.details)}</div>
        </>
      )}
      {data.digits != null && String(data.digits) !== '' && (
        <>
          <label>4 ספרות</label>
          <div className="mono mono-ltr" dir="ltr">
            {esc(data.digits)}
          </div>
        </>
      )}
    </>
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

  const postRevise = async (body: Record<string, unknown>) => {
    setStatus('מעדכן…');
    try {
      const r = await fetchJson(`${CAT}/api/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('עודכן.');
      await fetchNext();
    } catch (e) {
      setStatus(`שגיאה: ${e}`);
    }
  };

  const submit = async (body: Record<string, unknown>) => {
    setStatus('שולח…');
    try {
      const r = await fetchJson(`${CAT}/api/respond`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((r.data as { error?: string })?.error || r.status.toString());
      setStatus('נשמר.');
      await fetchNext();
    } catch (e) {
      setStatus(`שגיאה: ${e}`);
    }
  };

  const p = payload.pending || { kind: 'idle' };
  const sessionCats = payload.session_categories || [];
  const history = payload.history || [];

  const pendingOpts = useMemo(
    () => mergeCategoryOptions(sessionCats, p.kind !== 'idle' ? p : undefined),
    [sessionCats, p],
  );

  return (
    <div className="app-page app-page--narrow categorize-page">
      <h1>קטגוריזציה ידנית</h1>

      {history.length > 0 && (
        <section className="categorize-card">
          <h2>בסשן הנוכחי (ניתן לתקן)</h2>
          {history.map((item) => (
            <div key={item.prompt_id} className="hist-item">
              {item.kind === 'fluid' && (
                <>
                  <p className="mono">{esc(item.store_name)}</p>
                  <label>קטגוריה</label>
                  <CategoryCombo
                    inputId={`hcat-${item.prompt_id}`}
                    selected={(item.response && item.response.category) || ''}
                    options={mergeCategoryOptions(sessionCats, {
                      kind: 'fluid',
                      dynamic_categories: [],
                      all_categories: [],
                    } as Pending)}
                  />
                  <button
                    type="button"
                    className="small secondary"
                    onClick={() => {
                      const el = document.getElementById(`hcat-${item.prompt_id}`) as HTMLInputElement | null;
                      const cat = (el?.value || '').trim();
                      if (!cat) {
                        setStatus('בחר קטגוריה');
                        return;
                      }
                      void postRevise({ kind: 'fluid', prompt_id: item.prompt_id, category: cat });
                    }}
                  >
                    עדכן קטגוריה
                  </button>
                </>
              )}
              {item.kind === 'new_store' && (
                <>
                  <p className="mono">
                    {esc(item.store_name)} <span className="hint">(חנות חדשה)</span>
                  </p>
                  <label>קטגוריה</label>
                  <CategoryCombo
                    inputId={`hncat-${item.prompt_id}`}
                    selected={(item.response && item.response.category) || ''}
                    options={mergeCategoryOptions(sessionCats, {
                      kind: 'new_store',
                      all_categories: [],
                    } as Pending)}
                  />
                  <label>סוג</label>
                  <select
                    id={`hnst-${item.prompt_id}`}
                    defaultValue={item.response?.is_static === 0 ? '0' : '1'}
                  >
                    <option value="1">סטטית (1)</option>
                    <option value="0">דינמית (0)</option>
                  </select>
                  <button
                    type="button"
                    className="small secondary"
                    onClick={() => {
                      const el = document.getElementById(`hncat-${item.prompt_id}`) as HTMLInputElement | null;
                      const cat = (el?.value || '').trim();
                      if (!cat) {
                        setStatus('בחר קטגוריה');
                        return;
                      }
                      const nst = document.getElementById(`hnst-${item.prompt_id}`) as HTMLSelectElement | null;
                      const is_static = nst ? parseInt(nst.value, 10) : 1;
                      void postRevise({
                        kind: 'new_store',
                        prompt_id: item.prompt_id,
                        category: cat,
                        is_static,
                      });
                    }}
                  >
                    עדכן
                  </button>
                </>
              )}
              {item.kind === 'resolve_static' && (
                <>
                  <p>
                    <span className="mono">{esc(item.store_name)}</span> —{' '}
                    <span className="mono">{esc(item.category)}</span>
                  </p>
                  <div className="row2">
                    <button
                      type="button"
                      className="small secondary"
                      onClick={() =>
                        void postRevise({
                          kind: 'resolve_static',
                          prompt_id: item.prompt_id,
                          is_static: 0,
                        })
                      }
                    >
                      דינמית (0)
                    </button>
                    <button
                      type="button"
                      className="small"
                      onClick={() =>
                        void postRevise({
                          kind: 'resolve_static',
                          prompt_id: item.prompt_id,
                          is_static: 1,
                        })
                      }
                    >
                      סטטית (1)
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </section>
      )}

      <section className="categorize-card">
        {!p || p.kind === 'idle' ? (
          <>
            <p>אין כרגע שורה לקטגוריה.</p>
            <p className="hint">כשתופיע שורה חדשה, היא תוצג כאן לאחר שמירה או רענון.</p>
          </>
        ) : p.kind === 'fluid' ? (
          <>
            <p className="mono">{esc(p.store_name)}</p>
            <TransactionDetails data={p} />
            <label htmlFor="cat-main">קטגוריה</label>
            <CategoryCombo
              key={`fluid-cat-${String(p.prompt_id)}`}
              inputId="cat-main"
              selected=""
              options={pendingOpts}
            />
            <p className="hint">קטגוריות דינמיות קיימות: {esc((p.dynamic_categories || []).join(', '))}</p>
            <button
              type="button"
              onClick={() => {
                const el = document.getElementById('cat-main') as HTMLInputElement | null;
                const cat = (el?.value || '').trim();
                if (!cat) {
                  setStatus('בחר או הקלד קטגוריה');
                  return;
                }
                void submit({ kind: 'fluid', prompt_id: p.prompt_id, category: cat });
              }}
            >
              שמור
            </button>
          </>
        ) : p.kind === 'resolve_static' ? (
          <>
            <p>
              האם הקטגוריה <strong className="mono">{esc(p.category)}</strong> עבור{' '}
              <span className="mono">{esc(p.store_name)}</span> היא קבועה (סטטית)?
            </p>
            <TransactionDetails data={p} />
            <div className="row2">
              <button type="button" className="secondary" onClick={() => void submit({
                  kind: 'resolve_static',
                  prompt_id: p.prompt_id,
                  is_static: 0,
                })}
              >
                דינמית (0)
              </button>
              <button type="button" onClick={() => void submit({
                  kind: 'resolve_static',
                  prompt_id: p.prompt_id,
                  is_static: 1,
                })}
              >
                סטטית (1)
              </button>
            </div>
          </>
        ) : p.kind === 'new_store' ? (
          <>
            <p>
              חנות חדשה: <span className="mono">{esc(p.store_name)}</span>
            </p>
            <TransactionDetails data={p} />
            <label>קטגוריה</label>
            <CategoryCombo
              key={`new-${String(p.prompt_id)}`}
              inputId="ncat-main"
              selected=""
              options={pendingOpts}
            />
            <label>סוג</label>
            <select id="nst-main" defaultValue="1">
              <option value="1">סטטית (1)</option>
              <option value="0">דינמית (0)</option>
            </select>
            <button
              type="button"
              onClick={() => {
                const el = document.getElementById('ncat-main') as HTMLInputElement | null;
                const cat = (el?.value || '').trim();
                if (!cat) {
                  setStatus('בחר או הקלד קטגוריה');
                  return;
                }
                const nst = document.getElementById('nst-main') as HTMLSelectElement | null;
                void submit({
                  kind: 'new_store',
                  prompt_id: p.prompt_id,
                  category: cat,
                  is_static: nst ? parseInt(nst.value, 10) : 1,
                });
              }}
            >
              שמור
            </button>
          </>
        ) : (
          <p>מצב לא ידוע</p>
        )}
      </section>

      <p className="categorize-status">{status}</p>
    </div>
  );
}
