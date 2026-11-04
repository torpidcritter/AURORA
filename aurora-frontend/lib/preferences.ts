/**
 * Per-browser display preferences (ADR-049): colour theme, number and date format, and low-stock notifications.
 *
 * They live in localStorage because they describe this device, not the account; nothing here is sent to the API.
 * Every read and write tolerates blocked storage (private windows, strict browser settings): the defaults apply.
 */

export type Theme = "dark" | "light" | "system";
export const THEMES: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "Match system" },
];
export const THEME_KEY = "aurora.theme";
// Ledger, the light theme, is the default look (ADR-051); Night shift is the dark theme.
export const DEFAULT_THEME: Theme = "light";

/** Locales offered for numbers and dates. The interface text itself is English only (see docs/UX.md). */
export const LOCALES: { value: string; label: string }[] = [
  { value: "en-US", label: "English (United States): 1,234.5 · Jan 31, 2026" },
  { value: "en-GB", label: "English (United Kingdom): 1,234.5 · 31 Jan 2026" },
  { value: "en-IN", label: "English (India): 1,23,456.5 · 31 Jan 2026" },
  { value: "de-DE", label: "Deutsch: 1.234,5 · 31. Jan. 2026" },
  { value: "fr-FR", label: "Français: 1 234,5 · 31 janv. 2026" },
  { value: "es-ES", label: "Español: 1234,5 · 31 ene 2026" },
  { value: "ja-JP", label: "日本語: 1,234.5 · 2026年1月31日" },
];
export const LOCALE_KEY = "aurora.locale";
export const DEFAULT_LOCALE = "en-US";

export const NOTIFY_KEY = "aurora.notify.lowstock";

function read(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // storage blocked: the choice lasts for this page only
  }
}

export function isTheme(v: unknown): v is Theme {
  return v === "dark" || v === "light" || v === "system";
}

export function readTheme(): Theme {
  const v = read(THEME_KEY);
  return isTheme(v) ? v : DEFAULT_THEME;
}

/** The theme actually shown: "system" resolves through the operating system setting. */
export function resolvedTheme(t: Theme, prefersLight: boolean): "dark" | "light" {
  if (t === "system") return prefersLight ? "light" : "dark";
  return t;
}

export function applyTheme(t: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = t;
  const light = resolvedTheme(t, window.matchMedia?.("(prefers-color-scheme: light)").matches ?? false) === "light";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", light ? "#ffffff" : "#12161c");
}

export const THEME_EVENT = "aurora:theme-changed";

export function saveTheme(t: Theme): void {
  write(THEME_KEY, t);
  applyTheme(t);
  window.dispatchEvent(new Event(THEME_EVENT));
}

/**
 * Runs inline in <head> before the first paint, so a light-theme user never sees a dark flash. It must stay
 * self-contained (no imports) and must not throw.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t!=="light"&&t!=="dark"&&t!=="system")t=${JSON.stringify(DEFAULT_THEME)};document.documentElement.dataset.theme=t;}catch(e){document.documentElement.dataset.theme=${JSON.stringify(DEFAULT_THEME)};}})();`;

export function isSupportedLocale(v: unknown): v is string {
  return typeof v === "string" && LOCALES.some((l) => l.value === v);
}

export function readLocale(): string {
  const v = read(LOCALE_KEY);
  return isSupportedLocale(v) ? v : DEFAULT_LOCALE;
}

export function saveLocale(locale: string): void {
  write(LOCALE_KEY, isSupportedLocale(locale) && locale !== DEFAULT_LOCALE ? locale : null);
}

export function readNotify(): boolean {
  return read(NOTIFY_KEY) === "1";
}

export function saveNotify(on: boolean): void {
  write(NOTIFY_KEY, on ? "1" : null);
}
