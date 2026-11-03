import { act, fireEvent, render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DisplayPreferencesCard, KeyboardShortcuts, ThemeToggle } from "@/components/Preferences";
import { fmtDate, fmtInt, fmtMoney, formatLocale, setFormatLocale } from "@/lib/format";
import { alertsToNotify, notificationFor, readSeen, writeSeen } from "@/lib/notify";
import {
  DEFAULT_THEME,
  LOCALE_KEY,
  THEME_BOOT_SCRIPT,
  THEME_KEY,
  readLocale,
  readTheme,
  resolvedTheme,
  saveLocale,
  saveTheme,
} from "@/lib/preferences";
import { describeOverride } from "@/lib/purchasing";
import { GO_SHORTCUTS, isTypingTarget, shortcutFor } from "@/lib/shortcuts";
import type { Alert } from "@/lib/types";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/dashboard" }));

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  push.mockReset();
});
afterEach(() => setFormatLocale(null));

const alert = (o: Partial<Alert>): Alert => ({
  id: "a1",
  kind: "stockout_risk",
  severity: "high",
  status: "open",
  title: "SKU-1 may run out",
  message: "About 3 days of stock left.",
  product_id: "p1",
  details: {},
  created_at: "2026-11-01T10:00:00Z",
  updated_at: "2026-11-01T10:00:00Z",
  acknowledged_at: null,
  acknowledged_by: null,
  resolved_at: null,
  ...o,
});

describe("theme", () => {
  it("defaults to dark and ignores unknown stored values", () => {
    expect(readTheme()).toBe(DEFAULT_THEME);
    window.localStorage.setItem(THEME_KEY, "neon");
    expect(readTheme()).toBe("dark");
  });

  it("resolves match-system through the operating system setting", () => {
    expect(resolvedTheme("system", true)).toBe("light");
    expect(resolvedTheme("system", false)).toBe("dark");
    expect(resolvedTheme("light", false)).toBe("light");
  });

  it("boot script applies the saved theme before React and survives bad values", () => {
    window.localStorage.setItem(THEME_KEY, "light");
    new Function(THEME_BOOT_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe("light");
    window.localStorage.setItem(THEME_KEY, "<script>");
    new Function(THEME_BOOT_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("header toggle cycles dark, light, match system and saves the choice", () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /Theme: Dark/ });
    fireEvent.click(btn);
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: /Theme: Light/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Theme: Light/ }));
    expect(screen.getByRole("button", { name: /Theme: Match system/ })).toBeInTheDocument();
  });

  it("stays in step with a change made elsewhere", () => {
    render(<ThemeToggle />);
    act(() => saveTheme("system"));
    expect(screen.getByRole("button", { name: /Theme: Match system/ })).toBeInTheDocument();
  });
});

describe("number and date format", () => {
  it("formats with the chosen locale", () => {
    setFormatLocale("de-DE");
    expect(fmtInt(1234567)).toBe("1.234.567");
    expect(fmtDate("2026-01-31")).toContain("2026");
    expect(fmtMoney(1234)).toMatch(/1\.234/);
    expect(describeOverride("set", 1234.5)).toBe("1.234,5 units/day");
    setFormatLocale("en-IN");
    expect(fmtInt(123456)).toBe("1,23,456");
  });

  it("uses the default before the app has mounted, so the server and first client render match", () => {
    window.localStorage.setItem(LOCALE_KEY, "de-DE");
    expect(formatLocale()).toBe("en-US");
    expect(fmtInt(1234)).toBe("1,234");
  });

  it("stores only supported locales and drops the default", () => {
    saveLocale("de-DE");
    expect(readLocale()).toBe("de-DE");
    saveLocale("en-US");
    expect(window.localStorage.getItem(LOCALE_KEY)).toBeNull();
    window.localStorage.setItem(LOCALE_KEY, "xx-XX");
    expect(readLocale()).toBe("en-US");
  });
});

describe("keyboard shortcuts", () => {
  it("maps g-sequences, search and help, and ignores modified keys", () => {
    expect(shortcutFor({ key: "g" }, false)).toEqual({ type: "pending" });
    expect(shortcutFor({ key: "p" }, true)).toEqual({ type: "go", href: "/products" });
    expect(shortcutFor({ key: "z" }, true)).toEqual({ type: "none" });
    expect(shortcutFor({ key: "p" }, false)).toEqual({ type: "none" });
    expect(shortcutFor({ key: "/" }, false)).toEqual({ type: "search" });
    expect(shortcutFor({ key: "?" }, false)).toEqual({ type: "help" });
    expect(shortcutFor({ key: "g", ctrlKey: true }, false)).toEqual({ type: "none" });
    expect(shortcutFor({ key: "d", metaKey: true }, true)).toEqual({ type: "none" });
  });

  it("has one page per key", () => {
    const keys = GO_SHORTCUTS.map((s) => s.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("treats text fields as typing but not checkboxes or buttons", () => {
    const text = document.createElement("input");
    const box = Object.assign(document.createElement("input"), { type: "checkbox" });
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    expect(isTypingTarget(text)).toBe(true);
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
    expect(isTypingTarget(editable)).toBe(true);
    expect(isTypingTarget(box)).toBe(false);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });

  it("goes to a page with g then a key, and not while typing", () => {
    render(
      <>
        <input aria-label="Notes" />
        <KeyboardShortcuts />
      </>,
    );
    fireEvent.keyDown(screen.getByLabelText("Notes"), { key: "g" });
    fireEvent.keyDown(screen.getByLabelText("Notes"), { key: "p" });
    expect(push).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "o" });
    expect(push).toHaveBeenCalledWith("/purchase-orders");
  });

  it("opens the list with ? and closes it with Escape", () => {
    render(<KeyboardShortcuts />);
    fireEvent.keyDown(document.body, { key: "?" });
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(dialog).toHaveTextContent("Purchase orders");
    expect(screen.getByRole("button", { name: "Close shortcuts" })).toHaveFocus();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("focuses the page search box with /", () => {
    render(
      <>
        <input aria-label="Search products" data-shortcut-search />
        <KeyboardShortcuts />
      </>,
    );
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.getByLabelText("Search products")).toHaveFocus();
  });
});

describe("low-stock notifications", () => {
  it("picks new open high or critical stockout alerts, oldest first", () => {
    const items = [
      alert({ id: "new", created_at: "2026-11-02T00:00:00Z", severity: "critical" }),
      alert({ id: "old", created_at: "2026-11-01T00:00:00Z" }),
      alert({ id: "seen" }),
      alert({ id: "medium", severity: "medium" }),
      alert({ id: "drift", kind: "demand_drift" }),
      alert({ id: "acked", status: "acknowledged" }),
    ];
    expect(alertsToNotify(items, new Set(["seen"])).map((a) => a.id)).toEqual(["old", "new"]);
  });

  it("raises one notification for one alert and a summary for several", () => {
    expect(notificationFor([])).toBeNull();
    expect(notificationFor([alert({})])).toEqual({
      title: "Aurora: SKU-1 may run out",
      body: "About 3 days of stock left.",
      tag: "aurora-alert-a1",
    });
    const many = notificationFor([alert({ id: "x", severity: "critical" }), alert({ id: "y" })]);
    expect(many?.title).toBe("Aurora: 2 products at risk of running out");
    expect(many?.body).toBe("1 critical. Open Alerts to review them.");
  });

  it("remembers notified alerts and tolerates corrupt storage", () => {
    writeSeen(new Set(["a", "b"]));
    expect([...readSeen()]).toEqual(["a", "b"]);
    window.localStorage.setItem("aurora.notify.seen", "{not json");
    expect(readSeen().size).toBe(0);
  });
});

describe("display preferences card", () => {
  const renderCard = () =>
    render(
      <SWRConfig value={{ provider: () => new Map(), fetcher: async () => ({ items: [], total: 0, limit: 50, offset: 0 }), dedupingInterval: 0 }}>
        <DisplayPreferencesCard />
      </SWRConfig>,
    );

  it("changes the theme and explains what notifications can and cannot do", () => {
    renderCard();
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(screen.getByText(/Nothing is sent when every Aurora tab is closed/)).toBeInTheDocument();
    // jsdom has no Notification API.
    expect(screen.getByText("This browser does not support notifications.")).toBeInTheDocument();
  });

  it("turns notifications on without announcing alerts that were already open", async () => {
    const requestPermission = vi.fn(async () => "granted");
    vi.stubGlobal("Notification", Object.assign(vi.fn(), { permission: "default", requestPermission }));
    render(
      <SWRConfig
        value={{
          provider: () => new Map(),
          fetcher: async () => ({ items: [alert({ id: "already" })], total: 1, limit: 50, offset: 0 }),
          dedupingInterval: 0,
        }}
      >
        <DisplayPreferencesCard />
      </SWRConfig>,
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Allow low-stock notifications" })));
    expect(requestPermission).toHaveBeenCalled();
    expect(readSeen().has("already")).toBe(true);
    expect(window.localStorage.getItem("aurora.notify.lowstock")).toBe("1");
    vi.unstubAllGlobals();
  });
});
