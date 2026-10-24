/** Shared display formatting. Dates are always shown in UTC; costs keep 4 decimal places. */

/** Backend datetimes are UTC; tolerate a missing zone designator. */
function parseUtc(value: string): Date {
  return new Date(/(?:Z|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
}

export function formatUtc(value: string | null | undefined): string {
  if (!value) return "Never";
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-GB").format(value);
}

/** `$0.0000` from a Decimal serialised as a string or a number. */
export function formatUsd(value: string | number): string {
  const n = Number(value);
  return `$${(Number.isFinite(n) ? n : 0).toFixed(4)}`;
}

/** Cache hit rate arrives as a 0..1 ratio or a 0..100 percentage. */
export function formatPercent(value: string | number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.0%";
  return `${(n <= 1 ? n * 100 : n).toFixed(1)}%`;
}
