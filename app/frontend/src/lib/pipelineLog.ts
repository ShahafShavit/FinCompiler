/** Pipeline live log: cap by line count, not a huge character slice. */

export const PIPELINE_LOG_MAX_LINES = 2000;

export function appendCappedLog(
  prev: string,
  msg: string,
  maxLines: number = PIPELINE_LOG_MAX_LINES,
): string {
  const chunk = msg.endsWith('\n') ? msg : `${msg}\n`;
  const next = prev + chunk;
  let lines = 0;
  for (let i = 0; i < next.length; i++) {
    if (next.charCodeAt(i) === 10) lines += 1;
  }
  if (lines <= maxLines) return next;
  let skip = lines - maxLines;
  let start = 0;
  while (skip > 0) {
    const nl = next.indexOf('\n', start);
    if (nl < 0) break;
    start = nl + 1;
    skip -= 1;
  }
  return next.slice(start);
}
