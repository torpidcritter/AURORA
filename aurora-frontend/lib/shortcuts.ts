/**
 * Keyboard shortcuts (ADR-049). Two-key "g then x" sequences go to a page, "/" focuses the page's search box and
 * "?" lists the shortcuts. They never fire while the person is typing in a field, or with Ctrl, Alt or Cmd held,
 * so browser and screen-reader shortcuts keep working.
 */

export interface GoShortcut {
  key: string;
  href: string;
  label: string;
}

export const GO_SHORTCUTS: GoShortcut[] = [
  { key: "d", href: "/dashboard", label: "Dashboard" },
  { key: "a", href: "/alerts", label: "Alerts" },
  { key: "p", href: "/products", label: "Products" },
  { key: "f", href: "/forecasts", label: "Forecasts" },
  { key: "r", href: "/inventory", label: "Recommendations" },
  { key: "k", href: "/risk", label: "Stockout risk" },
  { key: "o", href: "/purchase-orders", label: "Purchase orders" },
  { key: "s", href: "/suppliers", label: "Suppliers" },
  { key: "c", href: "/assistant", label: "Assistant" },
  { key: "u", href: "/account", label: "Your account" },
];

/** How long after "g" the second key still counts. */
export const SEQUENCE_MS = 1500;

export type ShortcutAction =
  | { type: "none" }
  | { type: "pending" }
  | { type: "go"; href: string }
  | { type: "search" }
  | { type: "help" };

export interface KeyInput {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}

/** Decide what a key press does, given whether "g" was pressed just before. */
export function shortcutFor(e: KeyInput, gPending: boolean): ShortcutAction {
  if (e.ctrlKey || e.metaKey || e.altKey) return { type: "none" };
  if (gPending) {
    const hit = GO_SHORTCUTS.find((s) => s.key === e.key.toLowerCase());
    return hit ? { type: "go", href: hit.href } : { type: "none" };
  }
  if (e.key === "g" || e.key === "G") return { type: "pending" };
  if (e.key === "/") return { type: "search" };
  if (e.key === "?") return { type: "help" };
  return { type: "none" };
}

/** True when the key press belongs to a form field or editable text, where shortcuts must not interfere. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== "string") return false;
  const el = target as HTMLElement;
  const tag = el.tagName.toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (tag === "input") {
    const type = ((el as HTMLInputElement).type || "text").toLowerCase();
    return !["button", "checkbox", "radio", "submit", "reset", "range", "color", "file"].includes(type);
  }
  return el.isContentEditable || el.getAttribute("contenteditable") === "true";
}
