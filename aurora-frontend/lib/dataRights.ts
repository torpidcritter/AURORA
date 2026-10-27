import type { Job } from "@/lib/types";

/** What the organisation export list shows for one export job. */
export function exportState(job: Job, now: Date = new Date()): { label: string; downloadable: boolean } {
  if (job.status === "queued" || job.status === "running") return { label: "Preparing", downloadable: false };
  if (job.status !== "succeeded") return { label: job.status === "failed" ? "Failed" : "Cancelled", downloadable: false };
  const r = job.result ?? {};
  const expires = typeof r.expires_at === "string" ? new Date(r.expires_at) : null;
  if (r.expired || (expires && expires.getTime() <= now.getTime())) return { label: "Expired", downloadable: false };
  return { label: "Ready", downloadable: true };
}

/** Total rows in an export, from the per-table counts the job reports. */
export function exportRows(job: Job): number {
  const rows = job.result?.rows;
  if (!rows || typeof rows !== "object") return 0;
  return Object.values(rows as Record<string, unknown>).reduce<number>((n, v) => n + (typeof v === "number" ? v : 0), 0);
}

/** Whole days (rounded up) until a scheduled deletion; 0 once it is due. */
export function daysUntil(iso: string, now: Date = new Date()): number {
  const ms = new Date(iso).getTime() - now.getTime();
  return ms <= 0 ? 0 : Math.ceil(ms / 86_400_000);
}
