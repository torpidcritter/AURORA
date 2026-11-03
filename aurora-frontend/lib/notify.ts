/**
 * Low-stock browser notifications (ADR-049). While Aurora is open in a tab and the person has turned them on, new
 * high or critical stockout alerts raise a system notification. This is not web push: nothing arrives when every
 * Aurora tab is closed (see docs/UX.md for why).
 */
import type { Alert } from "@/lib/types";

export const POLL_MS = 120_000;
export const SEEN_KEY = "aurora.notify.seen";
const MAX_SEEN = 200;
const NOTIFY_SEVERITIES = new Set(["high", "critical"]);

export type NotifySupport = "unsupported" | "default" | "granted" | "denied";

export function notifySupport(): NotifySupport {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission as NotifySupport;
}

/** Open high or critical stockout alerts not notified before, oldest first. */
export function alertsToNotify(items: Alert[], seen: ReadonlySet<string>): Alert[] {
  return items
    .filter((a) => a.kind === "stockout_risk" && a.status === "open" && NOTIFY_SEVERITIES.has(a.severity) && !seen.has(a.id))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** The notification for one alert, or a single summary when several arrive at once. */
export function notificationFor(fresh: Alert[]): { title: string; body: string; tag: string } | null {
  if (!fresh.length) return null;
  if (fresh.length === 1) {
    const a = fresh[0]!;
    return { title: `Aurora: ${a.title}`, body: a.message, tag: `aurora-alert-${a.id}` };
  }
  const critical = fresh.filter((a) => a.severity === "critical").length;
  return {
    title: `Aurora: ${fresh.length} products at risk of running out`,
    body: critical ? `${critical} critical. Open Alerts to review them.` : "Open Alerts to review them.",
    tag: "aurora-alerts",
  };
}

export function readSeen(): Set<string> {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY);
    const ids = raw ? (JSON.parse(raw) as unknown) : [];
    return new Set(Array.isArray(ids) ? ids.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

export function writeSeen(seen: Set<string>): void {
  try {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-MAX_SEEN)));
  } catch {
    // storage blocked: alerts may be notified again after a reload
  }
}
