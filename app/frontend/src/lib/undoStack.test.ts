import { describe, expect, it } from 'vitest';

import { dropUndoEntry } from './undoStack';

describe('dropUndoEntry', () => {
  it('drops only the matching entry', () => {
    const last = { planId: 'p1' };
    const stack = [{ planId: 'p0' }, last];
    expect(dropUndoEntry(stack, last)).toEqual([{ planId: 'p0' }]);
    expect(dropUndoEntry(stack, { planId: 'nope' })).toEqual(stack);
  });
});
