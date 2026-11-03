"use client";

import { Keyboard, Monitor, Moon, Sun, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import useSWR from "swr";

import { Badge, Button, Card, Field, Notice, Select } from "@/components/ui";
import { fmtDate, fmtInt, fmtMoney, setFormatLocale } from "@/lib/format";
import { alertsToNotify, notificationFor, notifySupport, POLL_MS, readSeen, writeSeen, type NotifySupport } from "@/lib/notify";
import {
  DEFAULT_LOCALE,
  DEFAULT_THEME,
  LOCALES,
  THEMES,
  THEME_EVENT,
  applyTheme,
  readLocale,
  readNotify,
  readTheme,
  saveLocale,
  saveNotify,
  saveTheme,
  type Theme,
} from "@/lib/preferences";
import { GO_SHORTCUTS, SEQUENCE_MS, isTypingTarget, shortcutFor } from "@/lib/shortcuts";
import type { Alert, Page } from "@/lib/types";

const THEME_ICON = { dark: Moon, light: Sun, system: Monitor } as const;
const NEXT_THEME: Record<Theme, Theme> = { dark: "light", light: "system", system: "dark" };

// Saved preferences are read with useSyncExternalStore: the server render and hydration use the defaults, then the
// saved value, with no setState-in-effect round trip.
const NOTIFY_EVENT = "aurora:notify-changed";
const noopSubscribe = () => () => {};

function subscribeTheme(cb: () => void) {
  // Another control changed it, or the system setting flipped while "Match system" is on (updates the bar colour).
  const media = window.matchMedia?.("(prefers-color-scheme: light)");
  const onChange = () => {
    applyTheme(readTheme());
    cb();
  };
  window.addEventListener(THEME_EVENT, onChange);
  media?.addEventListener?.("change", onChange);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    media?.removeEventListener?.("change", onChange);
  };
}

function subscribeNotify(cb: () => void) {
  window.addEventListener(NOTIFY_EVENT, cb);
  return () => window.removeEventListener(NOTIFY_EVENT, cb);
}

export const useTheme = () => useSyncExternalStore(subscribeTheme, readTheme, () => DEFAULT_THEME);
const useLocale = () => useSyncExternalStore(noopSubscribe, readLocale, () => DEFAULT_LOCALE);
const useNotifyOn = () => useSyncExternalStore(subscribeNotify, readNotify, () => false);
const useNotifySupport = () => useSyncExternalStore(subscribeNotify, notifySupport, (): NotifySupport => "unsupported");

/** Header button that cycles dark → light → match system. The account page has the same choice as a list. */
export function ThemeToggle() {
  const theme = useTheme();
  const Icon = THEME_ICON[theme];
  const label = THEMES.find((t) => t.value === theme)?.label ?? theme;
  return (
    <button
      type="button"
      onClick={() => saveTheme(NEXT_THEME[theme])}
      className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink"
      aria-label={`Theme: ${label}. Change theme`}
      title={`Theme: ${label}`}
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}

/** Global keyboard shortcuts and their help dialog ("?"). */
export function KeyboardShortcuts() {
  const router = useRouter();
  const [help, setHelp] = useState(false);
  const pending = useRef<number | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return;
      if (help && e.key === "Escape") {
        setHelp(false);
        return;
      }
      const gPending = pending.current !== null && Date.now() - pending.current < SEQUENCE_MS;
      const action = shortcutFor(e, gPending);
      pending.current = null;
      switch (action.type) {
        case "pending":
          pending.current = Date.now();
          return;
        case "go":
          e.preventDefault();
          setHelp(false);
          router.push(action.href);
          return;
        case "search": {
          const box = document.querySelector<HTMLInputElement>("[data-shortcut-search]");
          if (box) {
            e.preventDefault();
            box.focus();
            box.select();
          }
          return;
        }
        case "help":
          e.preventDefault();
          returnTo.current = document.activeElement as HTMLElement | null;
          setHelp(true);
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [help, router]);

  useEffect(() => {
    if (help) closeRef.current?.focus();
    else returnTo.current?.focus?.();
  }, [help]);

  if (!help) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={() => setHelp(false)} aria-hidden />
      <div role="dialog" aria-modal="true" aria-labelledby="kbd-title" className="glass-strong relative w-full max-w-md rounded-xl border border-line p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="kbd-title" className="flex items-center gap-2 text-base font-semibold text-ink">
            <Keyboard className="h-4 w-4" aria-hidden /> Keyboard shortcuts
          </h2>
          <button ref={closeRef} type="button" onClick={() => setHelp(false)} className="rounded p-1.5 text-muted hover:text-ink" aria-label="Close shortcuts">
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          {GO_SHORTCUTS.map((s) => (
            <div key={s.key} className="contents">
              <dt><Keys keys={["g", s.key]} /></dt>
              <dd className="text-muted">{s.label}</dd>
            </div>
          ))}
          <dt><Keys keys={["/"]} /></dt>
          <dd className="text-muted">Search on this page (products, suppliers)</dd>
          <dt><Keys keys={["?"]} /></dt>
          <dd className="text-muted">Show this list</dd>
          <dt><Keys keys={["Esc"]} /></dt>
          <dd className="text-muted">Close</dd>
        </dl>
        <p className="mt-3 text-xs text-faint">Shortcuts are off while you type in a field.</p>
      </div>
    </div>
  );
}

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="inline-flex gap-1">
      {keys.map((k, i) => (
        <kbd key={i} className="min-w-6 rounded border border-line bg-surface-2 px-1.5 py-0.5 text-center font-mono text-xs text-ink">
          {k}
        </kbd>
      ))}
    </span>
  );
}

/** While a tab is open and notifications are on, raises one for each new high or critical stockout alert. */
export function LowStockNotifier() {
  const router = useRouter();
  const enabled = useNotifyOn();
  const on = useNotifySupport() === "granted" && enabled;
  const { data } = useSWR<Page<Alert>>(on ? "/alerts?status=open&kind=stockout_risk&limit=50" : null, { refreshInterval: POLL_MS });
  useEffect(() => {
    if (!on || !data) return;
    const seen = readSeen();
    const fresh = alertsToNotify(data.items, seen);
    const n = notificationFor(fresh);
    if (!n) return;
    fresh.forEach((a) => seen.add(a.id));
    writeSeen(seen);
    try {
      const note = new Notification(n.title, { body: n.body, tag: n.tag });
      note.onclick = () => {
        window.focus();
        router.push(`/alerts?status=open&severity=${fresh.some((a) => a.severity === "critical") ? "critical" : "high"}`);
      };
    } catch {
      // Some browsers only allow notifications from a service worker; the alerts page still lists them.
    }
  }, [on, data, router]);
  return null;
}

/** Account page card: theme, number and date format, and low-stock notifications. Saved in this browser only. */
export function DisplayPreferencesCard() {
  const theme = useTheme();
  const locale = useLocale();
  const notify = useNotifyOn();
  const support = useNotifySupport();
  const [busy, setBusy] = useState(false);
  const alerts = useSWR<Page<Alert>>(notify ? null : "/alerts?status=open&kind=stockout_risk&limit=50");

  const changeLocale = (l: string) => {
    saveLocale(l);
    setFormatLocale(l);
    // Formatted numbers are spread over every page; reloading is the simple way to redraw them all.
    window.location.reload();
  };

  const toggleNotify = async () => {
    if (notify) {
      saveNotify(false);
      window.dispatchEvent(new Event(NOTIFY_EVENT));
      return;
    }
    setBusy(true);
    try {
      const result = support === "granted" ? "granted" : await Notification.requestPermission();
      if (result === "granted") {
        // Alerts already open when notifications are turned on are not announced; only new ones are.
        const open = alerts.data ?? (await alerts.mutate());
        const seen = readSeen();
        open?.items.forEach((a) => seen.add(a.id));
        writeSeen(seen);
        saveNotify(true);
      }
      window.dispatchEvent(new Event(NOTIFY_EVENT));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Display and notifications" subtitle="Saved in this browser only.">
      <div className="space-y-4">
        <Field label="Theme" htmlFor="pref-theme">
          <Select
            id="pref-theme"
            value={theme}
            onChange={(e) => saveTheme(e.target.value as Theme)}
          >
            {THEMES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </Select>
        </Field>
        <Field
          label="Number and date format"
          htmlFor="pref-locale"
          hint={`For example ${fmtInt(1234567)} · ${fmtMoney(1234)} · ${fmtDate("2026-01-31")}. Screens stay in English.`}
        >
          <Select id="pref-locale" value={locale} onChange={(e) => changeLocale(e.target.value)}>
            {LOCALES.map((l) => (
              <option key={l.value} value={l.value}>{l.label}</option>
            ))}
          </Select>
        </Field>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
            Low-stock notifications {notify && support === "granted" ? <Badge tone="ok">On</Badge> : <Badge>Off</Badge>}
          </div>
          <p className="text-xs text-muted">
            A system notification when a product newly reaches high or critical stockout risk, checked every two minutes
            while Aurora is open in a tab. Nothing is sent when every Aurora tab is closed; daily digests cover that.
          </p>
          {support === "unsupported" ? (
            <Notice tone="warn">This browser does not support notifications.</Notice>
          ) : support === "denied" ? (
            <Notice tone="warn">Notifications are blocked for this site. Allow them in the browser&apos;s site settings, then come back.</Notice>
          ) : (
            <Button variant="secondary" onClick={toggleNotify} loading={busy}>
              {notify ? "Stop low-stock notifications" : "Allow low-stock notifications"}
            </Button>
          )}
        </div>
        <p className="text-xs text-faint">Press ? anywhere for keyboard shortcuts.</p>
      </div>
    </Card>
  );
}
