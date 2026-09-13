import { describe, expect, it } from 'vitest';

import { appendCappedLog } from './pipelineLog';

describe('appendCappedLog', () => {
  it('keeps the newest lines within the cap', () => {
    let s = '';
    for (let i = 0; i < 5; i++) s = appendCappedLog(s, `L${i}`, 3);
    expect(s).toBe('L2\nL3\nL4\n');
  });
});
