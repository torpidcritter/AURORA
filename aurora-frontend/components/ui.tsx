"use client";

import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

import { errorMessage } from "@/lib/api";
import type { RiskLevel, Severity } from "@/lib/types";

type Variant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  variant = "secondary",
  loading = false,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-primary text-slate-950 hover:bg-sky-300",
        variant === "secondary" && "border border-line bg-surface-2 text-ink hover:bg-slate-800",
        variant === "danger" && "bg-bad/90 text-slate-950 hover:bg-bad",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-ink",
        className,
      )}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

/** Shared glass surface (spec 9.2): translucent fill, 16px backdrop blur, hairline border and a faint glow. Solid when
 *  backdrop-filter is unsupported or the user prefers reduced transparency (see `glass` in app/globals.css). */
export const SURFACE = "glass rounded-xl border border-glass-line shadow-glass";

export function Card({ title, actions, children, className, subtitle }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx(SURFACE, "p-4 sm:p-5", className)}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "default", title }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "default" | "ok" | "warn" | "bad" | "ai"; title?: string }) {
  return (
    <div className={clsx(SURFACE, "p-4")} title={title}>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div
        className={clsx(
          "tabular mt-2 text-2xl font-semibold",
          tone === "ok" && "text-ok",
          tone === "warn" && "text-warn",
          tone === "bad" && "text-bad",
          tone === "ai" && "text-ai",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const RISK_STYLE: Record<string, string> = {
  critical: "bg-bad/15 text-bad ring-bad/30",
  high: "bg-orange-400/15 text-orange-300 ring-orange-400/30",
  medium: "bg-warn/15 text-warn ring-warn/30",
  low: "bg-ok/15 text-ok ring-ok/30",
  unknown: "bg-slate-500/15 text-slate-300 ring-slate-400/30",
};

export function RiskBadge({ level }: { level: RiskLevel | Severity | string }) {
  return (
    <span className={clsx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset", RISK_STYLE[level] ?? RISK_STYLE.unknown)}>
      {level}
    </span>
  );
}

export function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "ok" | "warn" | "bad" | "ai" | "info" }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        tone === "default" && "bg-slate-500/15 text-slate-300 ring-slate-400/25",
        tone === "ok" && "bg-ok/15 text-ok ring-ok/30",
        tone === "warn" && "bg-warn/15 text-warn ring-warn/30",
        tone === "bad" && "bg-bad/15 text-bad ring-bad/30",
        tone === "ai" && "bg-ai/15 text-ai ring-ai/30",
        tone === "info" && "bg-primary/15 text-primary ring-primary/30",
      )}
    >
      {children}
    </span>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-faint">{hint}</p>}
      {error && (
        <p className="text-xs text-bad" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-primary focus:outline-none disabled:opacity-60";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(inputCls, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(inputCls, "pr-8", props.className)} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-md bg-slate-700/30", className)} aria-hidden />;
}

export function LoadingBlock({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <Inbox className="h-6 w-6 text-faint" aria-hidden />
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const rid = (error as { requestId?: string | null } | null)?.requestId;
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-bad/30 bg-bad/10 p-4 text-sm">
      <div className="flex items-center gap-2 font-medium text-bad">
        <AlertTriangle className="h-4 w-4" aria-hidden /> {errorMessage(error)}
      </div>
      {rid && <div className="text-xs text-muted">Request ID: {rid}</div>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-4 w-4" aria-hidden /> Retry
        </Button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "ok" | "warn" | "bad"; children: ReactNode }) {
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={clsx(
        "rounded-lg border px-3 py-2 text-sm",
        tone === "info" && "border-primary/30 bg-primary/10 text-sky-200",
        tone === "ok" && "border-ok/30 bg-ok/10 text-emerald-200",
        tone === "warn" && "border-warn/30 bg-warn/10 text-amber-200",
        tone === "bad" && "border-bad/30 bg-bad/10 text-rose-200",
      )}
    >
      {children}
    </div>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-700/40" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full bg-ai transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="tabular mt-1 text-xs text-muted">{pct}%</div>
    </div>
  );
}

export function Table({ children, caption }: { children: ReactNode; caption?: string }) {
  return (
    <div className="-mx-4 overflow-x-auto sm:mx-0">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export function Th({ children, className, sort, onSort, dir }: { children?: ReactNode; className?: string; sort?: string; onSort?: (k: string) => void; dir?: "asc" | "desc" | null }) {
  const content = sort && onSort ? (
    <button type="button" onClick={() => onSort(sort)} className="inline-flex items-center gap-1 hover:text-ink">
      {children}
      <span aria-hidden className="text-faint">{dir === "asc" ? "▲" : dir === "desc" ? "▼" : "↕"}</span>
    </button>
  ) : (
    children
  );
  return (
    <th
      scope="col"
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}
      className={clsx("border-b border-line px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted", className)}
    >
      {content}
    </th>
  );
}

export function Td({ children, className, colSpan }: { children?: ReactNode; className?: string; colSpan?: number }) {
  return (
    <td colSpan={colSpan} className={clsx("border-b border-line/60 px-3 py-2.5 align-middle", className)}>
      {children}
    </td>
  );
}

export function Pagination({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <nav className="mt-4 flex items-center justify-between text-sm text-muted" aria-label="Pagination">
      <span className="tabular">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={page <= 1} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </nav>
  );
}

/** Button that asks for confirmation in an accessible modal dialog before running a destructive action. */
export function ConfirmButton({
  children,
  title,
  body,
  confirmLabel = "Confirm",
  onConfirm,
  variant = "danger",
  disabled,
}: {
  children: ReactNode;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  onConfirm: () => Promise<void> | void;
  variant?: Variant;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);
  return (
    <>
      <Button variant={variant === "danger" ? "ghost" : variant} onClick={() => setOpen(true)} disabled={disabled}>
        {children}
      </Button>
      <dialog
        ref={ref}
        aria-labelledby={`${id}-t`}
        onClose={() => setOpen(false)}
        className="glass-strong m-auto w-[min(92vw,420px)] rounded-xl border border-line p-5 text-ink shadow-2xl backdrop:bg-black/60"
      >
        {open && (
          <>
            <h2 id={`${id}-t`} className="text-base font-semibold">
              {title}
            </h2>
            <div className="mt-2 text-sm text-muted">{body}</div>
            {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
                Cancel
              </Button>
              <Button
                variant={variant}
                loading={busy}
                onClick={async () => {
                  setBusy(true);
                  setErr(null);
                  try {
                    await onConfirm();
                    setOpen(false);
                  } catch (e) {
                    setErr(e);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {confirmLabel}
              </Button>
            </div>
          </>
        )}
      </dialog>
    </>
  );
}
