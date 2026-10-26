"use client";

import clsx from "clsx";
import {
  Activity,
  Bell,
  Bot,
  BrainCircuit,
  CalendarRange,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  ScrollText,
  ShieldAlert,
  SlidersHorizontal,
  TrendingUp,
  Upload,
  Users,
  Warehouse,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import useSWR from "swr";

import { ChatProvider } from "@/components/chat";
import { ChatPanel, isChatPanelHidden } from "@/components/ChatPanel";
import { fetcher } from "@/lib/api";
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
      { href: "/signals", label: "External signals", icon: CalendarRange },
    ],
  },
  {
    section: "Inventory",
    items: [
      { href: "/inventory", label: "Recommendations", icon: Warehouse },
      { href: "/risk", label: "Stockout risk", icon: ShieldAlert },
      { href: "/scenarios", label: "Scenarios", icon: FlaskConical },
    ],
  },
  {
    section: "Intelligence",
    items: [
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
      { href: "/admin/settings", label: "Settings", icon: SlidersHorizontal, perm: "manage_settings" },
    ],
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { me, can } = useSession();
  const [open, setOpen] = useState(false);
  const { data: summary } = useSWR<{ open: number; by_severity: Record<string, number> }>("/alerts/summary", fetcher, {
    refreshInterval: 60_000,
  });

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST", headers: { "x-aurora-csrf": "1" } }).catch(() => undefined);
    router.replace("/login");
  }

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-5 px-3 py-4">
      {NAV.map((g) => {
        const items = g.items.filter((i) => !i.perm || can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.section}>
            <div className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">{g.section}</div>
            <ul className="space-y-0.5">
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
                        "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm",
                        active ? "bg-primary/10 text-primary" : "text-muted hover:bg-surface-2 hover:text-ink",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden />
                      <span className="flex-1">{i.label}</span>
                      {i.href === "/alerts" && summary && summary.open > 0 && (
                        <span className="tabular rounded-full bg-bad/20 px-1.5 text-xs text-bad" aria-label={`${summary.open} open alerts`}>
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
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-1 focus:text-slate-950">
          Skip to content
        </a>
        <aside className="glass sticky top-0 hidden h-screen overflow-y-auto border-r border-glass-line lg:block">
          <Brand />
          {nav}
        </aside>
        {open && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
            <aside className="glass-strong absolute inset-y-0 left-0 w-72 overflow-y-auto border-r border-line">
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
          <header className="glass-strong sticky top-0 z-30 flex items-center gap-3 border-b border-glass-line px-4 py-2.5 sm:px-6">
            <button type="button" className="rounded p-1.5 text-muted hover:text-ink lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0 flex-1 truncate text-sm text-muted">{me?.org_name}</div>
            {me && (
              <div className="flex items-center gap-3 text-sm">
                <span className="hidden text-muted sm:inline">{me.email}</span>
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs capitalize text-ink ring-1 ring-line">{me.role}</span>
                <button type="button" onClick={logout} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted hover:bg-surface-2 hover:text-ink">
                  <LogOut className="h-4 w-4" aria-hidden /> <span className="hidden sm:inline">Sign out</span>
                </button>
              </div>
            )}
          </header>
          {/* Extra bottom padding keeps the assistant launcher from covering the end of the page. */}
          <main id="main" className={clsx("mx-auto w-full max-w-[1400px] px-4 pt-6 sm:px-6", isChatPanelHidden(pathname) ? "pb-6" : "pb-24")}>
            {children}
          </main>
        </div>
        <ChatPanel />
      </div>
    </ChatProvider>
  );
}

function Brand() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2 px-5 py-4">
      <span className="h-6 w-6 rounded-md bg-gradient-to-br from-primary to-ai" aria-hidden />
      <span className="text-base font-semibold tracking-tight text-ink">Aurora</span>
    </Link>
  );
}
