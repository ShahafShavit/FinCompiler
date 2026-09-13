import { describe, expect, it } from 'vitest';

import { EXPLORE_PAGE_SIZE, explorePager, exploreQueryLimit } from './explorePager';

describe('explorePager', () => {
  it('uses the same page size for query and pager math', () => {
    expect(exploreQueryLimit()).toBe(EXPLORE_PAGE_SIZE);
    const p = explorePager(50, 80, exploreQueryLimit());
    expect(p.page).toBe(2);
    expect(p.pageCount).toBe(2);
    expect(p.canPrev).toBe(true);
    expect(p.canNext).toBe(false);
  });

  it('does not jump when many ids are listed', () => {
    const p = explorePager(0, 120, exploreQueryLimit());
    expect(p.pageSize).toBe(50);
    expect(p.canNext).toBe(true);
    expect(explorePager(50, 120).page).toBe(2);
  });
});
