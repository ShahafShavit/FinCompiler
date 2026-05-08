export type ApiResult<T> = {
  ok: boolean;
  status: number;
  data: T;
};

export async function fetchJson<T = unknown>(
  url: string,
  options?: RequestInit,
): Promise<ApiResult<T>> {
  const res = await fetch(url, options);
  const text = await res.text();
  let data: unknown = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(
      `HTTP ${res.status} — not JSON: ${text.replace(/\s+/g, ' ').slice(0, 220)}`,
    );
  }
  return { ok: res.ok, status: res.status, data: data as T };
}

export async function getJson<T = unknown>(url: string): Promise<T> {
  const r = await fetchJson<T>(url, { cache: 'no-store' });
  return r.data;
}

export async function postJson<T = unknown>(
  url: string,
  body: unknown = {},
): Promise<ApiResult<T>> {
  return fetchJson<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function formatMoney(v: number | null | undefined, suffix = '₪'): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1000) {
    return `${sign}${abs.toLocaleString(undefined, { maximumFractionDigits: 0 })}${suffix}`;
  }
  return `${sign}${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
}

export function formatPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}
