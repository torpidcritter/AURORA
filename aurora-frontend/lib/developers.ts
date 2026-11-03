/** Helpers for API keys and outgoing webhooks. The API enforces the same rules. */

import type { ApiKey, ApiKeyRole, Role } from "@/lib/types";

export const KEY_ROLES: { key: ApiKeyRole; label: string }[] = [
  { key: "viewer", label: "Viewer: read only" },
  { key: "operator", label: "Operator: also uploads, stock and purchasing" },
  { key: "analyst", label: "Analyst: also training, signals and overrides" },
];

export const EXPIRY_CHOICES: { days: number | null; label: string }[] = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "1 year" },
  { days: null, label: "Never" },
];

const RANK: Record<Role, number> = { viewer: 0, operator: 1, analyst: 2, admin: 3 };

/** Key roles a person may create: never above their own role, and never admin. */
export function rolesFor(role: Role | undefined): ApiKeyRole[] {
  if (!role) return [];
  return KEY_ROLES.map((r) => r.key).filter((k) => RANK[k] <= RANK[role]);
}

/** One-word state of a key for the list. */
export function keyState(k: ApiKey, now: Date = new Date()): "active" | "revoked" | "expired" {
  if (k.revoked_at) return "revoked";
  if (k.expires_at && new Date(k.expires_at).getTime() <= now.getTime()) return "expired";
  return "active";
}

/** Plain reason a webhook URL will be refused, checked before sending (the server re-checks and resolves DNS). */
export function webhookUrlProblem(url: string): string | null {
  const v = url.trim();
  if (!v) return "Enter the address to send events to";
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return "That is not a web address";
  }
  if (u.protocol !== "https:") return "The address must start with https://";
  if (u.username || u.password) return "The address must not contain a user name or password";
  return null;
}
