/** Response from ``GET /api/ledger-meta`` (file stat only). */
export type LedgerMeta = {
  ok: boolean;
  exists: boolean;
  mtime_ns: number | null;
  error?: string;
};

export type DashboardSummary = {
  ok: boolean;
  ledger_exists: boolean;
  kpis: {
    net_worth_latest: number | null;
    net_worth_latest_date: string | null;
    net_worth_prev_month: number | null;
    net_worth_prev_month_date: string | null;
    net_worth_delta_mom: number | null;
    income_30d: number | null;
    expense_30d: number | null;
    net_30d: number | null;
    savings_rate_30d: number | null;
    uncategorized_count: number | null;
    transaction_count: number | null;
    last_ingest_date: string | null;
  };
};

export type AllocationRow = { activity_type: string; balance_ils: number };
export type AllocationTimelineRow = {
  as_of_date: string;
  activity_type: string;
  balance_ils: number;
};
export type CashflowRow = {
  month: string;
  income: number;
  expense: number;
  net: number;
  expenseNeg?: number;
};
export type CategoryPeriodStatRow = {
  category: string;
  income: number;
  expense: number;
  net: number;
  txn_count: number;
  pct_of_period_income: number;
  pct_of_period_expense: number;
};
export type CategoryPeriodStatsResponse = {
  ok: boolean;
  ledger_exists: boolean;
  period: string;
  limit: number;
  period_income_total: number;
  period_expense_total: number;
  rows: CategoryPeriodStatRow[];
  category_bucket_count?: number;
  start_ym?: string;
  end_ym?: string;
  window_label?: string;
};

export type MonthBoundsResponse = {
  ok: boolean;
  ledger_exists: boolean;
  min_ym: string | null;
  max_ym: string | null;
};
export type SourceCategoryMatrixResponse = {
  ok: boolean;
  ledger_exists: boolean;
  months: number;
  direction: string;
  top_sources: number;
  top_categories: number;
  sources: string[];
  categories: string[];
  cells: number[][];
  row_totals: number[];
  col_totals: number[];
  grand_total: number;
};
export type SourceRow = {
  source: string;
  count: number;
  expense: number;
  income: number;
};

export type RowsResponse<T> = {
  ok: boolean;
  ledger_exists: boolean;
  rows: T[];
  [key: string]: unknown;
};
