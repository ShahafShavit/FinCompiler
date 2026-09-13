import { describe, expect, it } from 'vitest';

import { acquireLock, releaseLock } from './saveLock';

describe('saveLock', () => {
  it('rejects a second acquire until released', () => {
    const flag = { current: false };
    expect(acquireLock(flag)).toBe(true);
    expect(acquireLock(flag)).toBe(false);
    releaseLock(flag);
    expect(acquireLock(flag)).toBe(true);
  });
});
