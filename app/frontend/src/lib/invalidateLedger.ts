import type { QueryClient } from '@tanstack/react-query';

import { getActiveBookId } from './api';

/** After a ledger row edit, drop Dashboard/Heatmap caches for the active book. */
export function invalidateLedgerViews(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({
    predicate: (query) =>
      query.queryKey.includes('ledger-meta') || query.queryKey.includes('dashboard'),
  });
  void fetch('/heatmap/api/refresh', {
    method: 'POST',
    headers: { 'X-Finance-Book': getActiveBookId() },
  }).catch(() => {
    /* heatmap bundle cache is best-effort */
  });
}
