"use client";

import clsx from "clsx";
import { createContext, useContext, useEffect, useId, useRef, useState, type FormEvent, type ReactNode, type Ref } from "react";

import { Badge, Button, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

/** Shared pieces of the assistant: the /assistant page and the floating ChatPanel both use them. */

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  meta?: Pick<ChatResponse, "mode" | "tool_calls" | "notice">;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export const CHAT_SUGGESTIONS = [
  "Which products are at risk of stocking out?",
  "Summarise open alerts",
  "Is any product's demand drifting?",
  "What should I reorder this week?",
];
/** Limits enforced by POST /chat (ChatIn): message length, history turns and the length of one turn. */
export const CHAT_MAX_LEN = 2000;
const HISTORY_TURNS = 10;
const TURN_MAX_LEN = 8000;

/** The last turns sent as context, within the API's limits. */
export function chatHistory(msgs: ChatMsg[]): ChatTurn[] {
  return msgs.slice(-HISTORY_TURNS).map(({ role, content }) => ({ role, content: content.slice(0, TURN_MAX_LEN) }));
}

/** Asks the assistant through the BFF proxy (`POST /api/proxy/chat`). */
export function postChat(message: string, history: ChatTurn[]): Promise<ChatResponse> {
  return api<ChatResponse>("/chat", { method: "POST", body: { message, history } });
}

export interface ChatState {
  msgs: ChatMsg[];
  busy: boolean;
  error: unknown;
  /** Sends a message. Returns false when it was not sent (empty, or an answer is still pending). */
  send: (content: string) => boolean;
}

function useChatState(): ChatState {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // A ref as well as state, so two sends in the same tick (double Enter) cannot both go out.
  const inFlight = useRef(false);

  const send = (content: string): boolean => {
    const message = content.trim().slice(0, CHAT_MAX_LEN);
    if (!message || inFlight.current) return false;
    inFlight.current = true;
    setError(null);
    const history = chatHistory(msgs);
    setMsgs((m) => [...m, { role: "user", content: message }]);
    setBusy(true);
    postChat(message, history)
      .then((r) =>
        setMsgs((m) => [...m, { role: "assistant", content: r.answer, meta: { mode: r.mode, tool_calls: r.tool_calls, notice: r.notice } }]),
      )
      .catch((e: unknown) => setError(e))
      .finally(() => {
        inFlight.current = false;
        setBusy(false);
      });
    return true;
  };

  return { msgs, busy, error, send };
}

const ChatContext = createContext<ChatState | null>(null);

/** One conversation for the signed-in session, shared by the overlay and the full page. It lives in memory only and is
 *  dropped when the signed-in layout unmounts (sign-out or session expiry). */
export function ChatProvider({ children }: { children: ReactNode }) {
  const state = useChatState();
  return <ChatContext.Provider value={state}>{children}</ChatContext.Provider>;
}

/** The shared conversation, or a local one when rendered outside a ChatProvider. */
export function useChat(): ChatState {
  const shared = useContext(ChatContext);
  const local = useChatState();
  return shared ?? local;
}

function Bubble({ m }: { m: ChatMsg }) {
  return (
    <div className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
      <div
        className={
          m.role === "user"
            ? "max-w-[85%] rounded-xl bg-primary/15 px-4 py-2 text-sm text-ink"
            : "max-w-[90%] rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-ink"
        }
      >
        <div className="whitespace-pre-wrap break-words">{m.content}</div>
        {m.meta && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
            <Badge tone={m.meta.mode === "claude" ? "ai" : "default"}>{m.meta.mode === "claude" ? "Claude" : "Built-in summary"}</Badge>
            {m.meta.tool_calls.length > 0 && <span>Data used: {m.meta.tool_calls.map((t) => t.tool.replace(/_/g, " ")).join(", ")}</span>}
            {m.meta.notice && <span>{m.meta.notice}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

/** The conversation log with prompt starters. `compact` keeps scrolling inside the log (for the floating panel)
 *  instead of scrolling the page. */
export function ChatLog({ msgs, busy, onSuggestion, compact = false }: { msgs: ChatMsg[]; busy: boolean; onSuggestion: (s: string) => void; compact?: boolean }) {
  const logRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (compact) {
      const el = logRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    } else {
      endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
    }
  }, [msgs, busy, compact]);

  return (
    <div ref={logRef} className={clsx("space-y-4 overflow-y-auto", compact ? "min-h-0 flex-1 p-4" : "flex-1")} role="log" aria-live="polite" aria-label="Conversation">
      {msgs.length === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-muted">Try one of these:</p>
          <div className={compact ? "flex flex-col items-stretch gap-2" : "flex flex-wrap gap-2"}>
            {CHAT_SUGGESTIONS.map((s) => (
              <Button key={s} variant="secondary" className={compact ? "justify-start text-left" : undefined} onClick={() => onSuggestion(s)}>
                {s}
              </Button>
            ))}
          </div>
        </div>
      )}
      {msgs.map((m, i) => (
        <Bubble key={i} m={m} />
      ))}
      {busy && (
        <p className="text-sm text-muted" role="status">
          Thinking…
        </p>
      )}
      <div ref={endRef} />
    </div>
  );
}

/** Message box: Enter sends, Shift+Enter adds a line. The text is cleared only when the message was accepted. */
export function ChatComposer({
  onSend,
  busy,
  inputRef,
  id,
  compact = false,
}: {
  onSend: (text: string) => boolean;
  busy: boolean;
  inputRef?: Ref<HTMLTextAreaElement>;
  id?: string;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const autoId = useId();
  const inputId = id ?? `${autoId}-chat`;
  const submit = () => {
    if (onSend(text)) setText("");
  };
  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        submit();
      }}
      className={clsx("flex gap-2", compact ? "items-end" : "mt-4")}
    >
      <label htmlFor={inputId} className="sr-only">
        Message
      </label>
      <textarea
        id={inputId}
        ref={inputRef}
        rows={2}
        maxLength={CHAT_MAX_LEN}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={compact ? "Ask about stock, risk or alerts" : "Ask a question (Enter to send, Shift+Enter for a new line)"}
        className="min-w-0 flex-1 resize-none rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-primary focus:outline-none"
      />
      <Button type="submit" variant="primary" loading={busy} disabled={!text.trim()}>
        Send
      </Button>
    </form>
  );
}

export const CHAT_DISCLAIMER = "Answers are generated from your data and may be wrong. Check figures on the relevant page before acting.";

export function ChatDisclaimer({ compact = false }: { compact?: boolean }) {
  return compact ? <p className="text-xs text-faint">{CHAT_DISCLAIMER}</p> : <Notice>{CHAT_DISCLAIMER}</Notice>;
}
