/** Undo stack: drop an entry only after the inverse action succeeds. */

export function dropUndoEntry<T>(stack: T[], entry: T): T[] {
  const i = stack.lastIndexOf(entry);
  if (i < 0) return stack;
  return stack.slice(0, i).concat(stack.slice(i + 1));
}
