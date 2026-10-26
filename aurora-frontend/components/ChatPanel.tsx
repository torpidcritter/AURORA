"use client";

import { Bot, Maximize2, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { ChatComposer, ChatDisclaimer, ChatLog, useChat } from "@/components/chat";
import { ErrorState } from "@/components/ui";
import { useSession } from "@/lib/session";

/** Pages that already show the full assistant (or redirect to it), where the overlay would duplicate it. */
const HIDDEN_ON = ["/assistant", "/chat"];

export const isChatPanelHidden = (pathname: string | null): boolean =>
  HIDDEN_ON.some((p) => pathname === p || (pathname ?? "").startsWith(`${p}/`));

/**
 * Floating assistant overlay (spec 9.1): a launcher fixed bottom-right that opens a non-modal dialog talking to the
 * same `POST /chat` endpoint as the /assistant page, with the same conversation. Escape or the close button closes it
 * and returns focus to the launcher. The page stays usable while it is open.
 */
export function ChatPanel() {
  const pathname = usePathname();
  const { can } = useSession();
  const { msgs, busy, error, send } = useChat();
  const [open, setOpen] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const panelId = useId();
  const titleId = useId();

  // The backend enforces the permission on every call; this only hides the launcher from accounts that lack it.
  const hidden = isChatPanelHidden(pathname) || !can("read");
  const isOpen = open && !hidden;

  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
  }, [isOpen]);

  if (hidden) return null;

  const close = () => {
    setOpen(false);
    launcherRef.current?.focus();
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  };

  return (
    <div className="print:hidden">
      {isOpen && (
        <div
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-labelledby={titleId}
          onKeyDown={onKeyDown}
          className="glass-strong fixed bottom-20 right-4 z-30 flex h-[min(600px,calc(100dvh-7rem))] w-[min(420px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border border-line shadow-2xl shadow-black/50"
        >
          <header className="flex items-center gap-2 border-b border-line px-4 py-3">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-ai/15 text-ai" aria-hidden>
              <Bot className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="text-sm font-semibold text-ink">
                Assistant
              </h2>
              <p className="truncate text-xs text-muted">Reads your data with your permissions. Cannot change anything.</p>
            </div>
            <Link
              href="/assistant"
              onClick={() => setOpen(false)}
              className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink"
              aria-label="Open the full assistant page"
              title="Open the full assistant page"
            >
              <Maximize2 className="h-4 w-4" aria-hidden />
            </Link>
            <button type="button" onClick={close} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Close assistant">
              <X className="h-4 w-4" aria-hidden />
            </button>
          </header>
          <ChatLog compact msgs={msgs} busy={busy} onSuggestion={(s) => void send(s)} />
          <div className="space-y-2 border-t border-line p-3">
            {error != null && <ErrorState error={error} />}
            <ChatComposer compact onSend={send} busy={busy} inputRef={inputRef} />
            <ChatDisclaimer compact />
          </div>
        </div>
      )}
      <button
        ref={launcherRef}
        type="button"
        onClick={() => (isOpen ? close() : setOpen(true))}
        onKeyDown={isOpen ? onKeyDown : undefined}
        aria-label="Ask the assistant"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? panelId : undefined}
        title="Ask the assistant"
        className="fixed bottom-4 right-4 z-30 inline-flex h-12 w-12 items-center justify-center rounded-full bg-ai text-slate-950 shadow-lg shadow-ai/25 ring-1 ring-white/10 transition-colors hover:bg-violet-300"
      >
        {isOpen ? <X className="h-5 w-5" aria-hidden /> : <Bot className="h-5 w-5" aria-hidden />}
      </button>
    </div>
  );
}
