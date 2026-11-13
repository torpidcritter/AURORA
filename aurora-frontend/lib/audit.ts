/** Query strings for the audit log list and export. Dates are whole UTC days; the end day is included. */

export interface AuditFilters {
  action: string;
  actor: string;
  outcome: string;
  from: string; // yyyy-mm-dd or ""
  to: string;
}

export function dateRangeError(from: string, to: string): string | null {
  return from && to && to < from ? "The end date must be on or after the start date" : null;
}

export function auditParams(f: AuditFilters): URLSearchParams {
  const qs = new URLSearchParams();
  if (f.action.trim()) qs.set("action", f.action.trim());
  if (f.actor.trim()) qs.set("actor", f.actor.trim());
  if (f.outcome) qs.set("outcome", f.outcome);
  if (f.from) qs.set("since", `${f.from}T00:00:00Z`);
  if (f.to) {
    const next = new Date(`${f.to}T00:00:00Z`);
    next.setUTCDate(next.getUTCDate() + 1);
    qs.set("until", next.toISOString());
  }
  return qs;
}
