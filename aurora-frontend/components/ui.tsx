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
        "inline-flex min-h-8 items-center justify-center gap-2 rounded-sm border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "border-primary bg-primary font-semibold text-on-accent hover:bg-primary/90",
        variant === "secondary" && "border-line-strong bg-surface text-ink hover:bg-hover",
        variant === "danger" && "border-bad bg-bad font-semibold text-on-accent hover:bg-bad/90",
        variant === "ghost" && "border-transparent text-primary hover:bg-hover",
        className,
      )}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

/** Shared panel surface (Ledger, ADR-051): solid paper, one hairline border, a small radius and no shadow. */
export const SURFACE = "rounded-sm border border-line bg-surface";

export function Card({ title, actions, children, className, subtitle }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx(SURFACE, "p-4 sm:p-5", className)}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 max-w-3xl text-xs text-muted">{subtitle}</p>}
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
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-[22px]">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "default", title }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "default" | "ok" | "warn" | "bad" | "ai"; title?: string }) {
  return (
    <div className={clsx(SURFACE, "p-4")} title={title}>
      <div className="ledger-label">{label}</div>
      <div
        className={clsx(
          "tabular mt-2 font-mono text-[22px] font-medium tracking-tight",
          tone === "ok" && "text-ok",
          tone === "warn" && "text-warn",
          tone === "bad" && "text-bad",
          tone === "ai" && "text-ink",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

// Ledger status mark: a small square in the status colour beside the word, never colour alone. Critical is also bold.
const RISK_STYLE: Record<string, string> = {
  critical: "text-bad font-semibold before:bg-bad",
  high: "text-chart-high before:bg-chart-high",
  medium: "text-warn before:bg-warn",
  low: "text-ok before:bg-ok",
  unknown: "text-muted before:bg-line-strong",
};

export function RiskBadge({ level }: { level: RiskLevel | Severity | string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-xs font-medium capitalize before:h-2 before:w-2 before:shrink-0 before:content-['']", RISK_STYLE[level] ?? RISK_STYLE.unknown)}>
      {level}
    </span>
  );
}

export function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "ok" | "warn" | "bad" | "ai" | "info" }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-px text-xs font-medium",
        tone === "default" && "border-line-strong bg-surface-2 text-muted",
        tone === "ok" && "border-ok/35 bg-ok/8 text-ok",
        tone === "warn" && "border-warn/35 bg-warn/8 text-warn",
        tone === "bad" && "border-bad/35 bg-bad/8 text-bad",
        tone === "ai" && "border-line-strong bg-surface text-ink",
        tone === "info" && "border-primary/30 bg-primary/6 text-primary",
      )}
    >
      {children}
    </span>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-xs font-medium text-ink/80">
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
  "rounded-sm border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-faint focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:bg-surface-2 disabled:opacity-70";
// Full width unless the caller sets a width (both utilities in one class list would leave the winner to CSS order).
const width = (className?: string) => (/(^|\s)w-/.test(className ?? "") ? null : "w-full");

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(inputCls, width(props.className), props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(inputCls, width(props.className), "pr-8", props.className)} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-sm bg-surface-2", className)} aria-hidden />;
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
    <div className="flex flex-col items-center justify-center gap-2 rounded-sm border border-dashed border-line-strong px-6 py-10 text-center">
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
    <div role="alert" className="flex flex-col items-start gap-2 rounded-sm border border-bad/30 border-l-[3px] border-l-bad bg-bad/5 p-4 text-sm">
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
        "rounded-sm border border-l-[3px] px-3 py-2 text-sm",
        tone === "info" && "border-line border-l-primary bg-surface-2 text-ink",
        tone === "ok" && "border-ok/25 border-l-ok bg-ok/5 text-ink",
        tone === "warn" && "border-warn/25 border-l-warn bg-warn/5 text-ink",
        tone === "bad" && "border-bad/25 border-l-bad bg-bad/5 text-ink",
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
      <div className="h-1.5 w-full overflow-hidden rounded-sm bg-surface-2 ring-1 ring-inset ring-line" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="tabular mt-1 text-xs text-muted">{pct}%</div>
    </div>
  );
}

export function Table({ children, caption }: { children: ReactNode; caption?: string }) {
  // relative: screen-reader-only text in header cells is absolutely positioned and must stay inside this scroll box,
  // or it widens the page on a phone.
  return (
    <div className="relative -mx-4 overflow-x-auto sm:mx-0">
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
      className={clsx("ledger-label border-b-[3px] border-double border-rule bg-surface-2/60 px-3 py-2 text-left align-bottom", className)}
    >
      {content}
    </th>
  );
}

export function Td({ children, className, colSpan }: { children?: ReactNode; className?: string; colSpan?: number }) {
  return (
    <td colSpan={colSpan} className={clsx("border-b border-line px-3 py-2 align-middle", className)}>
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
        className="m-auto w-[min(92vw,420px)] rounded-sm border border-line-strong bg-surface p-5 text-ink shadow-lg backdrop:bg-[#0e1116]/50"
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
