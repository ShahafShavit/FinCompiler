import { describe, expect, it } from 'vitest';

import { isAbortError } from './api';

describe('isAbortError', () => {
  it('detects DOM AbortError', () => {
    expect(isAbortError(new DOMException('aborted', 'AbortError'))).toBe(true);
    expect(isAbortError(new Error('nope'))).toBe(false);
  });
});
