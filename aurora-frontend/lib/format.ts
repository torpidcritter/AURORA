import { DEFAULT_LOCALE, readLocale } from "@/lib/preferences";

// Numbers and dates follow the locale chosen on the account page (ADR-049). The server render and the first client
// render both use the default, so hydration matches; the chosen locale applies from then on.
let locale: string | null = null;
const cache = new Map<string, Intl.NumberFormat>();
let hydrated = false;

/** Called once the app has mounted; formatting switches to the saved locale from the next render. */
export function markHydrated(): void {
  hydrated = true;
}

/** The locale used for formatting. Read from the browser once, after hydration. */
export function formatLocale(): string {
  if (locale) return locale;
  if (typeof window === "undefined" || !hydrated) return DEFAULT_LOCALE;
  locale = readLocale();
  return locale;
}

/** For tests and the account page: format with this locale from now on. */
export function setFormatLocale(l: string | null): void {
  locale = l;
}

function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const loc = formatLocale();
  const key = `${loc}|${JSON.stringify(opts)}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(loc, opts);
    cache.set(key, f);
  }
  return f;
}

const ok = (v: number | null | undefined): v is number => v != null && Number.isFinite(v);

export const fmtInt = (v: number | null | undefined): string => (ok(v) ? nf({ maximumFractionDigits: 0 }).format(v) : "—");
export const fmt1 = (v: number | null | undefined): string => (ok(v) ? nf({ maximumFractionDigits: 1 }).format(v) : "—");
export const fmt2 = (v: number | null | undefined): string => (ok(v) ? nf({ maximumFractionDigits: 2 }).format(v) : "—");
// Amounts are stored without a currency; USD is shown until organisations can set theirs (see docs/UX.md).
export const fmtMoney = (v: number | null | undefined): string =>
  ok(v) ? nf({ style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v) : "—";
export const fmtMoney2 = (v: number | null | undefined): string =>
  ok(v) ? nf({ style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v) : "—";
export const fmtPct = (v: number | null | undefined, digits = 0): string =>
  v == null || !Number.isFinite(v) ? "—" : `${(v * 100).toFixed(digits)}%`;
export const fmtPctPoints = (v: number | null | undefined, digits = 1): string =>
  v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(digits)}%`;

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso);
  return d.toLocaleDateString(formatLocale(), { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtShortDate(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return d.toLocaleDateString(formatLocale(), { month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(formatLocale(), {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export const METHOD_LABELS: Record<string, string> = {
  auto: "Auto (best per product)",
  transformer: "Transformer",
  sma: "Moving average",
  ses: "Exponential smoothing",
  holt_winters: "Holt-Winters",
  launch_analogue: "Launch plan (analogue products)",
  seasonal_naive: "Seasonal naive",
  croston_sba: "Croston (intermittent)",
};

export const methodLabel = (m: string | null | undefined): string => (m ? (METHOD_LABELS[m] ?? m) : "—");
