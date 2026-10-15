import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchJson } from './api';
import { useBook } from './book';

export function categorizeSummaryKey(bookId: string) {
  return [bookId, 'categorize-summary'] as const;
}

export type CategorizeSummary = {
  open_count?: number;
  pending_count?: number;
  count?: number;
  compiled_exists?: boolean;
};

export function openCountFromSummary(s: CategorizeSummary | undefined): number | null {
  if (!s) return null;
  const v = s.open_count ?? s.pending_count ?? s.count;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  return null;
}

export async function fetchCategorizeSummary(): Promise<CategorizeSummary> {
  const r = await fetchJson<CategorizeSummary>('/categorize/api/summary', {
    cache: 'no-store',
  });
  if (!r.ok) throw new Error(`categorize summary: HTTP ${r.status}`);
  return r.data;
}

/** Shared queue count for sidebar badge, Pipeline hint, and Categorize invalidation. */
export function useCategorizeSummary() {
  const { bookId } = useBook();
  return useQuery({
    queryKey: categorizeSummaryKey(bookId),
    queryFn: fetchCategorizeSummary,
    staleTime: 20_000,
    refetchOnWindowFocus: true,
  });
}

export function useInvalidateCategorizeSummary() {
  const qc = useQueryClient();
  const { bookId } = useBook();
  return useCallback(
    () => void qc.invalidateQueries({ queryKey: categorizeSummaryKey(bookId) }),
    [qc, bookId],
  );
}
