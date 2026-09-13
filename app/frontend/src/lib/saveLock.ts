/** In-flight save lock so Enter / double-click cannot POST twice. */

export function acquireLock(flag: { current: boolean }): boolean {
  if (flag.current) return false;
  flag.current = true;
  return true;
}

export function releaseLock(flag: { current: boolean }): void {
  flag.current = false;
}
