const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export const fmtInt = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? "—" : nf0.format(v));
export const fmt1 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? "—" : nf1.format(v));
export const fmt2 = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? "—" : nf2.format(v));
export const fmtMoney = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? "—" : money.format(v));
export const fmtPct = (v: number | null | undefined, digits = 0): string =>
  v == null || !Number.isFinite(v) ? "—" : `${(v * 100).toFixed(digits)}%`;
export const fmtPctPoints = (v: number | null | undefined, digits = 1): string =>
  v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(digits)}%`;

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = iso.length === 10 ? new Date(`${iso}T00:00:00Z`) : new Date(iso);
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtShortDate(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
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
  seasonal_naive: "Seasonal naive",
  croston_sba: "Croston (intermittent)",
};

export const methodLabel = (m: string | null | undefined): string => (m ? (METHOD_LABELS[m] ?? m) : "—");
