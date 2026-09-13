import type { QueryClient } from '@tanstack/react-query';

/** After a ledger row edit, drop Dashboard/Heatmap caches keyed on ledger mtime. */
export function invalidateLedgerViews(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: ['ledger-meta'] });
  void queryClient.invalidateQueries({ queryKey: ['dashboard'] });
  void fetch('/heatmap/api/refresh', { method: 'POST' }).catch(() => {
    /* heatmap bundle cache is best-effort */
  });
}
