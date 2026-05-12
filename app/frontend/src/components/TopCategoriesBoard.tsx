import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

import {
  DndContext,
  DragOverlay,
  type DragEndEvent,
  type DragStartEvent,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { putJson } from '../lib/api';

export type TopCatColumn = { top_name: string; sub_categories: string[] };

type BoardState = { unassigned: string[]; columns: { top_name: string; subs: string[] }[] };

function itemId(sub: string): string {
  return `cat:${encodeURIComponent(sub)}`;
}

function parseItemId(id: string): string | null {
  if (!id.startsWith('cat:')) return null;
  try {
    return decodeURIComponent(id.slice(4));
  } catch {
    return null;
  }
}

function dropId(colKey: string): string {
  return `drop:${colKey}`;
}

function parseDropId(id: string): string | null {
  if (!id.startsWith('drop:')) return null;
  return id.slice(5);
}

function cloneBoard(src: BoardState): BoardState {
  return {
    unassigned: [...src.unassigned],
    columns: src.columns.map((c) => ({ top_name: c.top_name, subs: [...c.subs] })),
  };
}

function findSubLocation(sub: string, b: BoardState): { area: 'u' | 'c'; colIdx: number; idx: number } | null {
  const ui = b.unassigned.indexOf(sub);
  if (ui >= 0) return { area: 'u', colIdx: -1, idx: ui };
  for (let ci = 0; ci < b.columns.length; ci++) {
    const ji = b.columns[ci].subs.indexOf(sub);
    if (ji >= 0) return { area: 'c', colIdx: ci, idx: ji };
  }
  return null;
}

function SortableChip({ sub }: { sub: string }) {
  const id = itemId(sub);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.28 : 1,
  };
  return (
    <button
      ref={setNodeRef}
      style={style}
      type="button"
      className="intg-top-chip"
      {...attributes}
      {...listeners}
    >
      {sub}
    </button>
  );
}

function ColumnShell({
  colKey,
  title,
  subs,
  onDeleteColumn,
}: {
  colKey: string;
  title: string;
  subs: string[];
  onDeleteColumn?: () => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: dropId(colKey) });
  return (
    <div className={`intg-top-column${isOver ? ' intg-top-column--over' : ''}`}>
      <div className="intg-top-column-hd">
        <span className="intg-top-column-title">{title}</span>
        {onDeleteColumn ? (
          <button type="button" className="intg-btn intg-btn--small intg-btn--secondary" onClick={onDeleteColumn}>
            Remove
          </button>
        ) : null}
      </div>
      <SortableContext id={colKey} items={subs.map(itemId)} strategy={verticalListSortingStrategy}>
        <div ref={setNodeRef} className="intg-top-column-body">
          {subs.length === 0 ? <p className="intg-top-column-empty">Drag categories here</p> : null}
          {subs.map((s) => (
            <SortableChip key={s} sub={s} />
          ))}
        </div>
      </SortableContext>
    </div>
  );
}

function boardFromServer(columns: TopCatColumn[], unassigned: string[]): BoardState {
  return {
    unassigned: [...unassigned],
    columns: columns.map((c) => ({ top_name: c.top_name, subs: [...c.sub_categories] })),
  };
}

export default function TopCategoriesBoard({
  ledgerExists,
  columns: initialCols,
  unassigned: initialUnassigned,
  onSaved,
}: {
  ledgerExists: boolean;
  columns: TopCatColumn[];
  unassigned: string[];
  onSaved: () => void;
}) {
  const [board, setBoard] = useState<BoardState>(() => boardFromServer(initialCols, initialUnassigned));
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeDragLabel, setActiveDragLabel] = useState<string | null>(null);
  const boardScrollRef = useRef<HTMLDivElement>(null);
  const dragActiveRef = useRef(false);

  const serverKey = useMemo(
    () =>
      JSON.stringify({
        c: initialCols.map((x) => [x.top_name, x.sub_categories]),
        u: initialUnassigned,
      }),
    [initialCols, initialUnassigned],
  );

  useEffect(() => {
    setBoard(boardFromServer(initialCols, initialUnassigned));
  }, [serverKey]);

  useEffect(() => {
    const EDGE = 52;
    const MAX_STEP = 32;

    const onPointerMove = (e: PointerEvent) => {
      if (!dragActiveRef.current) return;
      const root = boardScrollRef.current;
      if (!root || root.scrollWidth <= root.clientWidth + 1) return;

      const rect = root.getBoundingClientRect();
      if (e.clientX < rect.left + EDGE) {
        const t = Math.min(1, (rect.left + EDGE - e.clientX) / EDGE);
        root.scrollLeft -= MAX_STEP * t;
      } else if (e.clientX > rect.right - EDGE) {
        const t = Math.min(1, (e.clientX - (rect.right - EDGE)) / EDGE);
        root.scrollLeft += MAX_STEP * t;
      }
    };

    window.addEventListener('pointermove', onPointerMove, { passive: true });
    return () => window.removeEventListener('pointermove', onPointerMove);
  }, []);

  const autoScrollOpts = useMemo(
    () => ({
      threshold: { x: 0.12, y: 0.12 },
      acceleration: 14,
      interval: 5,
    }),
    [],
  );

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
  );

  const onDragStart = useCallback((event: DragStartEvent) => {
    dragActiveRef.current = true;
    setActiveDragLabel(parseItemId(String(event.active.id)));
  }, []);

  const clearDragUi = useCallback(() => {
    dragActiveRef.current = false;
    setActiveDragLabel(null);
  }, []);

  const onDragEnd = useCallback((event: DragEndEvent) => {
    clearDragUi();
    const { active, over } = event;
    if (!over) return;
    const activeSub = parseItemId(String(active.id));
    if (!activeSub) return;

    const overRaw = String(over.id);
    const overSub = parseItemId(overRaw);
    const overDrop = parseDropId(overRaw);

    setBoard((prev) => {
      const b = cloneBoard(prev);
      const from = findSubLocation(activeSub, b);
      if (!from) return prev;

      if (overSub && overSub !== activeSub) {
        const to = findSubLocation(overSub, b);
        if (!to) return prev;

        if (from.area === 'u' && to.area === 'u') {
          const oldIndex = from.idx;
          const newIndex = to.idx;
          b.unassigned = arrayMove(b.unassigned, oldIndex, newIndex);
          return b;
        }
        if (from.area === 'c' && to.area === 'c' && from.colIdx === to.colIdx) {
          const subs = b.columns[from.colIdx].subs;
          b.columns[from.colIdx].subs = arrayMove(subs, from.idx, to.idx);
          return b;
        }

        if (from.area === 'u') b.unassigned.splice(from.idx, 1);
        else b.columns[from.colIdx].subs.splice(from.idx, 1);

        if (to.area === 'u') {
          b.unassigned.splice(to.idx, 0, activeSub);
        } else {
          b.columns[to.colIdx].subs.splice(to.idx, 0, activeSub);
        }
        return b;
      }

      if (overDrop !== null) {
        if (from.area === 'u') b.unassigned.splice(from.idx, 1);
        else b.columns[from.colIdx].subs.splice(from.idx, 1);

        if (overDrop === '__unassigned__') {
          b.unassigned.push(activeSub);
        } else {
          const ci = b.columns.findIndex((c) => c.top_name === overDrop);
          if (ci >= 0) b.columns[ci].subs.push(activeSub);
        }
        return b;
      }

      return prev;
    });
  }, [clearDragUi]);

  const onSave = async () => {
    setBusy(true);
    setMsg(null);
    const body = {
      columns: board.columns.map((c) => ({ top_name: c.top_name, sub_categories: c.subs })),
    };
    const r = await putJson<Record<string, unknown>>('/api/integrity/top-categories', body);
    setBusy(false);
    const p = r.data;
    if (!r.ok || p.ok === false) {
      setMsg(typeof p.message === 'string' ? p.message : `HTTP ${r.status}`);
      return;
    }
    setMsg('Saved.');
    onSaved();
  };

  const addColumn = () => {
    const name = window.prompt('New column name (top category label)?', '');
    if (name === null) return;
    const t = name.trim();
    if (!t) return;
    setBoard((prev) => {
      if (prev.columns.some((c) => c.top_name === t)) {
        window.alert('A column with that name already exists.');
        return prev;
      }
      return { ...prev, columns: [...prev.columns, { top_name: t, subs: [] }] };
    });
  };

  const removeColumn = (idx: number) => {
    setBoard((prev) => {
      const c = prev.columns[idx];
      if (!c) return prev;
      return {
        unassigned: [...prev.unassigned, ...c.subs],
        columns: prev.columns.filter((_, i) => i !== idx),
      };
    });
  };

  if (!ledgerExists) {
    return null;
  }

  return (
    <section className="intg-card">
      <h2 className="intg-card-title">Top categories (categorize navigation)</h2>
      <p className="intg-hint">
        Group store category names into columns for the categorize queue only. This does not change how categories are
        stored on transactions. Drag chips between columns, add columns as needed, then Save.
      </p>
      <div className="intg-top-toolbar">
        <button type="button" className="intg-btn intg-btn--secondary" onClick={addColumn}>
          Add column
        </button>
        <button type="button" className="intg-btn" disabled={busy} onClick={() => void onSave()}>
          {busy ? 'Saving…' : 'Save layout'}
        </button>
      </div>
      {msg ? <p className="intg-msg">{msg}</p> : null}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        autoScroll={autoScrollOpts}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={clearDragUi}
      >
        <div ref={boardScrollRef} className="intg-top-board">
          <ColumnShell colKey="__unassigned__" title="Unassigned" subs={board.unassigned} />
          {board.columns.map((c, idx) => (
            <ColumnShell
              key={c.top_name}
              colKey={c.top_name}
              title={c.top_name}
              subs={c.subs}
              onDeleteColumn={() => removeColumn(idx)}
            />
          ))}
        </div>
        <DragOverlay>
          {activeDragLabel ? (
            <div className="intg-top-chip intg-top-chip--drag-overlay" role="presentation">
              {activeDragLabel}
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </section>
  );
}
