"use client";

import clsx from "clsx";
import {
  Activity,
  Bell,
  Bot,
  BrainCircuit,
  CalendarRange,
  ClipboardList,
  CloudDownload,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LogOut,
  MapPin,
  Menu,
  Package,
  PackageX,
  PenLine,
  Percent,
  Rocket,
  ScrollText,
  ShieldAlert,
  SlidersHorizontal,
  TrendingUp,
  Wallet,
  Newspaper,
  Layers,
  Truck,
  Upload,
  Users,
  Warehouse,
  Webhook,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import useSWR from "swr";

import { ChatProvider } from "@/components/chat";
import { ChatPanel, isChatPanelHidden } from "@/components/ChatPanel";
import { DeletionBanner } from "@/components/DataRights";
import { KeyboardShortcuts, LowStockNotifier, ThemeToggle } from "@/components/Preferences";
import { fetcher } from "@/lib/api";
import { markHydrated } from "@/lib/format";
import { DEFAULT_LOCALE, readLocale } from "@/lib/preferences";
import { useSession } from "@/lib/session";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Gauge;
  perm?: string;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/alerts", label: "Alerts", icon: Bell },
    ],
  },
  {
    section: "Data & demand",
    items: [
      { href: "/upload", label: "Upload data", icon: Upload },
      { href: "/products", label: "Products", icon: Package },
      { href: "/forecasts", label: "Forecasts", icon: TrendingUp },
      { href: "/category-forecasts", label: "Category forecasts", icon: Layers },
      { href: "/overrides", label: "Forecast overrides", icon: PenLine },
      { href: "/launches", label: "Launches", icon: Rocket },
      { href: "/promotions", label: "Promotions", icon: Percent },
      { href: "/signals", label: "External signals", icon: CalendarRange },
    ],
  },
  {
    section: "Inventory",
    items: [
      { href: "/inventory", label: "Recommendations", icon: Warehouse },
      { href: "/risk", label: "Stockout risk", icon: ShieldAlert },
      { href: "/overstock", label: "Overstock", icon: PackageX },
      { href: "/locations", label: "Locations", icon: MapPin },
      { href: "/scenarios", label: "Scenarios", icon: FlaskConical },
    ],
  },
  {
    section: "Purchasing",
    items: [
      { href: "/purchase-orders", label: "Purchase orders", icon: ClipboardList },
      { href: "/budget-plan", label: "Order on a budget", icon: Wallet },
      { href: "/suppliers", label: "Suppliers", icon: Truck },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { href: "/digests", label: "Digests", icon: Newspaper },
      { href: "/models", label: "Models & drift", icon: BrainCircuit },
      { href: "/assistant", label: "Assistant", icon: Bot },
    ],
  },
  {
    section: "Administration",
    items: [
      { href: "/admin/users", label: "Users", icon: Users, perm: "manage_users" },
      { href: "/admin/audit", label: "Audit log", icon: ScrollText, perm: "view_audit" },
      { href: "/admin/ops", label: "System & integrations", icon: Activity, perm: "view_ops" },
      { href: "/admin/imports", label: "Data imports", icon: CloudDownload, perm: "manage_integrations" },
      { href: "/admin/developers", label: "API and webhooks", icon: Webhook, perm: "manage_integrations" },
      { href: "/admin/settings", label: "Settings", icon: SlidersHorizontal, perm: "manage_settings" },
    ],
  },
];

const noopSubscribe = () => () => {};
// While hydrating, React renders the server value and calls this only afterwards; on a client-side mount nothing is
// hydrated. Either way, from this call on formatting may use the saved locale.
function clientLocale(): string {
  markHydrated();
  return readLocale();
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { me, can } = useSession();
  const [open, setOpen] = useState(false);
  // Numbers render with the default locale until mount (so hydration matches), then the page redraws once in the
  // saved locale. Keyed on the locale so every formatted value below re-renders.
  const fmtKey = useSyncExternalStore(noopSubscribe, clientLocale, () => DEFAULT_LOCALE);
  const { data: summary } = useSWR<{ open: number; by_severity: Record<string, number> }>("/alerts/summary", fetcher, {
    refreshInterval: 60_000,
  });

  // An organisation that requires two-factor login sends accounts without it to set it up (the API enforces this too).
  const mustSetUp = !!me?.two_factor_required && pathname !== "/account";
  useEffect(() => {
    if (mustSetUp) router.replace("/account");
  }, [mustSetUp, router]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST", headers: { "x-aurora-csrf": "1" } }).catch(() => undefined);
    router.replace("/login");
  }

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-4 py-3">
      {NAV.map((g) => {
        const items = g.items.filter((i) => !i.perm || can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.section}>
            <div className="ledger-label px-5 pb-1 text-faint">{g.section}</div>
            <ul>
              {items.map((i) => {
                const active = pathname === i.href || pathname.startsWith(`${i.href}/`);
                const Icon = i.icon;
                return (
                  <li key={i.href}>
                    <Link
                      href={i.href}
                      onClick={() => setOpen(false)}
                      aria-current={active ? "page" : undefined}
                      className={clsx(
                        "flex items-center gap-2.5 px-5 py-1.5 text-sm",
                        active ? "bg-hover font-semibold text-ink shadow-[inset_3px_0_0_var(--color-primary)]" : "text-muted hover:bg-hover hover:text-ink",
                      )}
                    >
                      <Icon className={clsx("h-4 w-4 shrink-0", active ? "text-primary" : "text-faint")} strokeWidth={1.75} aria-hidden />
                      <span className="flex-1">{i.label}</span>
                      {i.href === "/alerts" && summary && summary.open > 0 && (
                        <span className="font-mono text-[11px] font-medium text-bad" aria-label={`${summary.open} open alerts`}>
                          {summary.open}
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );

  return (
    <ChatProvider>
      <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-1 focus:text-on-accent">
          Skip to content
        </a>
        <aside className="sticky top-0 hidden h-screen overflow-y-auto border-r border-line bg-nav lg:block">
          <Brand />
          {nav}
        </aside>
        {open && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div className="absolute inset-0 bg-[#0e1116]/50" onClick={() => setOpen(false)} />
            <aside className="absolute inset-y-0 left-0 w-72 overflow-y-auto border-r border-line bg-nav">
              <div className="flex items-center justify-between pr-3">
                <Brand />
                <button type="button" onClick={() => setOpen(false)} className="rounded p-2 text-muted hover:text-ink" aria-label="Close navigation">
                  <X className="h-5 w-5" />
                </button>
              </div>
              {nav}
            </aside>
          </div>
        )}
        <div className="min-w-0">
          <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-surface px-4 sm:px-6">
            <button type="button" className="rounded p-1.5 text-muted hover:text-ink lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0 flex-1 truncate text-sm text-muted">{me?.org_name}</div>
            {me && (
              <div className="flex items-center gap-2 text-sm sm:gap-3">
                <ThemeToggle />
                <Link href="/account" className="hidden text-muted hover:text-ink hover:underline sm:inline" title="Account and security">{me.email}</Link>
                <span className="ledger-label rounded-sm border border-line-strong px-1.5 py-px">{me.role}</span>
                <button type="button" onClick={logout} className="inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-muted hover:bg-hover hover:text-ink">
                  <LogOut className="h-4 w-4" aria-hidden /> <span className="hidden sm:inline">Sign out</span>
                </button>
              </div>
            )}
          </header>
          {/* Extra bottom padding keeps the assistant launcher from covering the end of the page. */}
          <main key={fmtKey} id="main" className={clsx("mx-auto w-full max-w-[1400px] px-4 pt-6 sm:px-6", isChatPanelHidden(pathname) ? "pb-6" : "pb-24")}>
            {me?.org_deletion_due_at && <DeletionBanner dueAt={me.org_deletion_due_at} />}
            {children}
          </main>
        </div>
        <ChatPanel />
        <KeyboardShortcuts />
        <LowStockNotifier />
      </div>
    </ChatProvider>
  );
}

function Brand() {
  return (
    <Link href="/dashboard" className="flex h-14 items-center gap-2.5 border-b border-line px-5">
      <span className="h-3.5 w-3.5 bg-primary" aria-hidden />
      <span className="text-[15px] font-semibold tracking-wide text-ink">Aurora</span>
    </Link>
  );
}
