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

export type NetWorthRow = { as_of_date: string; total_ils: number };
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
};
export type TopCategoryRow = { category: string; amount: number };
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
