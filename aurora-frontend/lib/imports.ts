/** Scheduled data imports (ADR-050). The API validates everything again and checks addresses against private
 *  networks; these helpers only give early, plain feedback. */

export type ImportKind = "file_url" | "woocommerce";
export type ImportStatus = "imported" | "unchanged" | "failed";

export interface ImportSource {
  id: string;
  name: string;
  kind: ImportKind;
  url_hint: string;
  has_credential: boolean;
  options: { missing_policy?: "zero" | "interpolate"; lookback_days?: number };
  frequency: "hourly" | "daily";
  hour_utc: number;
  is_active: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: ImportStatus | null;
  last_message: string | null;
  consecutive_failures: number;
  version: number;
  created_at: string | null;
}

export interface ImportRun {
  id: string;
  trigger: "schedule" | "manual";
  status: ImportStatus;
  rows: number;
  products: number;
  dataset_id: string | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export const KIND_LABEL: Record<ImportKind, string> = {
  file_url: "File at a web address",
  woocommerce: "WooCommerce store",
};

export const STATUS_TONE: Record<ImportStatus, "ok" | "default" | "bad"> = {
  imported: "ok",
  unchanged: "default",
  failed: "bad",
};

export interface ImportForm {
  name: string;
  kind: ImportKind;
  url: string;
  authorization: string;
  consumerKey: string;
  consumerSecret: string;
  missingPolicy: "zero" | "interpolate";
  lookbackDays: string;
  frequency: "hourly" | "daily";
  hourUtc: string;
}

export const EMPTY_FORM: ImportForm = {
  name: "",
  kind: "file_url",
  url: "",
  authorization: "",
  consumerKey: "",
  consumerSecret: "",
  missingPolicy: "zero",
  lookbackDays: "14",
  frequency: "daily",
  hourUtc: "5",
};

export function importFormErrors(f: ImportForm): Partial<Record<keyof ImportForm, string>> {
  const e: Partial<Record<keyof ImportForm, string>> = {};
  if (!f.name.trim()) e.name = "Give the import a name";
  const url = f.url.trim();
  let u: URL | null = null;
  try {
    u = new URL(url);
  } catch {
    e.url = url ? "That is not a web address" : "Enter the address";
  }
  if (u) {
    if (u.protocol !== "https:") e.url = "The address must start with https://";
    else if (u.username || u.password) e.url = "Put credentials in the fields below, not in the address";
    else if (f.kind === "woocommerce" && (u.search || u.hash)) e.url = "Enter the store's address only, for example https://shop.example.com";
  }
  if (f.kind === "woocommerce") {
    if (!f.consumerKey.trim()) e.consumerKey = "Enter the consumer key";
    if (!f.consumerSecret.trim()) e.consumerSecret = "Enter the consumer secret";
    const d = Number(f.lookbackDays);
    if (!Number.isInteger(d) || d < 1 || d > 90) e.lookbackDays = "Between 1 and 90 days";
  } else if (/[\r\n]/.test(f.authorization)) {
    e.authorization = "One line only";
  }
  return e;
}

/** The request body for creating a source from the form. */
export function importBody(f: ImportForm) {
  const woo = f.kind === "woocommerce";
  const credential = woo
    ? { consumer_key: f.consumerKey.trim(), consumer_secret: f.consumerSecret.trim() }
    : f.authorization.trim()
      ? { authorization: f.authorization.trim() }
      : null;
  return {
    name: f.name.trim(),
    kind: f.kind,
    url: f.url.trim(),
    credential,
    options: woo ? { lookback_days: Number(f.lookbackDays) } : { missing_policy: f.missingPolicy },
    frequency: f.frequency,
    hour_utc: Number(f.hourUtc),
  };
}

/** When the source runs, in words. */
export function scheduleLabel(s: Pick<ImportSource, "frequency" | "hour_utc" | "is_active">): string {
  if (!s.is_active) return "Paused";
  return s.frequency === "hourly" ? "Every hour" : `Daily at ${String(s.hour_utc).padStart(2, "0")}:00 UTC`;
}
