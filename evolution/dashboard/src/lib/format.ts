export function shortId(s: string | null | undefined): string {
  return s ? s.slice(0, 8) : "n/a";
}

export function fmt(
  n: number | null | undefined,
  decimals = 2,
): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "n/a";
  return n.toFixed(decimals);
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
