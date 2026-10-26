"use client";

import { Check, Copy, KeyRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, Notice } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import type { ResetLink } from "@/lib/types";

/** Shows a freshly issued one-time reset link. It lives only in React state: closing the panel discards it and the
 *  server cannot show it again (it stores only a hash). */
export function ResetLinkPanel({ email, link, onDone }: { email: string; link: ResetLink; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "manual">("idle");
  useEffect(() => {
    panel.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, []);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(link.reset_url);
      setCopy("copied");
    } catch {
      // No clipboard access (e.g. plain HTTP on a non-local address): select the text for a manual copy.
      input.current?.select();
      setCopy("manual");
    }
  };

  return (
    <section ref={panel} aria-label={`Reset link for ${email}`} className="mb-4 space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        <KeyRound className="h-4 w-4 text-primary" aria-hidden /> One-time reset link for {email}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="reset-link-url" className="sr-only">Reset link</label>
        <input
          id="reset-link-url"
          ref={input}
          readOnly
          value={link.reset_url}
          onFocus={(e) => e.currentTarget.select()}
          className="w-full rounded-lg border border-line bg-bg/60 px-3 py-2 font-mono text-xs text-ink focus:border-primary focus:outline-none"
        />
        <Button variant="secondary" onClick={() => void copyLink()}>
          {copy === "copied" ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
          {copy === "copied" ? "Copied" : "Copy link"}
        </Button>
      </div>
      {copy === "manual" && <p className="text-xs text-muted">Copying is blocked here. The link is selected: press Ctrl+C (⌘C on a Mac).</p>}
      <p className="text-xs text-muted">
        Works once and expires <span className="text-ink">{fmtDateTime(link.expires_at)}</span>. Any earlier link for this user no longer works.
      </p>
      <Notice tone="warn">
        Anyone with this link can set this user&apos;s password. Send it only over a trusted channel (in person, by phone, or a direct
        message to them), never in a shared channel or ticket. It will not be shown again.
      </Notice>
      <div className="flex justify-end">
        <Button variant="ghost" onClick={onDone}>Done</Button>
      </div>
    </section>
  );
}
