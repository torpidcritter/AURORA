/** Pure helpers for locations and transfers. The API enforces the same rules. */

import type { LocationStock } from "@/lib/types";

export const LOCATION_CODE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/;

export function locationErrors(v: { code: string; name: string }): string | null {
  if (!LOCATION_CODE.test(v.code.trim())) return "Code: up to 40 letters, digits, dots, dashes or underscores, starting with a letter or digit";
  if (!v.name.trim()) return "Name is required";
  return null;
}

export function transferErrors(t: { from: string; to: string; quantity: string }, levels: LocationStock[]): string | null {
  if (!t.from || !t.to) return "Choose where to move stock from and to";
  if (t.from === t.to) return "Pick two different locations";
  if (!/^\d+$/.test(t.quantity.trim()) || Number(t.quantity) < 1) return "Enter a whole number of units";
  const src = levels.find((l) => l.location_id === t.from);
  if (src && Number(t.quantity) > src.on_hand) return `Only ${src.on_hand} on hand at ${src.code}`;
  return null;
}
