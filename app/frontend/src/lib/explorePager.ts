/** Shared Explore pager math: query limit and UI page counts must use the same size. */

export const EXPLORE_PAGE_SIZE = 50;

export function exploreQueryLimit(): number {
  return EXPLORE_PAGE_SIZE;
}

export function explorePager(
  offset: number,
  total: number,
  pageSize: number = EXPLORE_PAGE_SIZE,
): {
  page: number;
  pageCount: number;
  canPrev: boolean;
  canNext: boolean;
  pageSize: number;
} {
  const size = Math.max(1, Math.floor(pageSize) || EXPLORE_PAGE_SIZE);
  const off = Math.max(0, Math.floor(offset) || 0);
  const tot = Math.max(0, Math.floor(total) || 0);
  return {
    page: Math.floor(off / size) + 1,
    pageCount: Math.max(1, Math.ceil(tot / size)),
    canPrev: off > 0,
    canNext: off + size < tot,
    pageSize: size,
  };
}
