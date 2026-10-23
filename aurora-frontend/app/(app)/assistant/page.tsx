"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Badge, Button, Card, ErrorState, Notice, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  meta?: Pick<ChatResponse, "mode" | "tool_calls" | "notice">;
}

const SUGGESTIONS = [
  "Which products are at risk of stocking out?",
  "Summarise open alerts",
  "Is any product's demand drifting?",
  "What should I reorder this week?",
];
const MAX_LEN = 2000;

export default function AssistantPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [msgs, busy]);

  const send = async (content: string) => {
    const message = content.trim();
    if (!message || busy) return;
    setErr(null);
    const history = msgs.slice(-10).map(({ role, content: c }) => ({ role, content: c }));
    setMsgs((m) => [...m, { role: "user", content: message }]);
    setText("");
    setBusy(true);
    try {
      const r = await api<ChatResponse>("/chat", { method: "POST", body: { message, history } });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, meta: { mode: r.mode, tool_calls: r.tool_calls, notice: r.notice } }]);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(text);
  };

  return (
    <>
      <PageHeader
        title="Assistant"
        description="Ask about your products, risk, alerts and drift. The assistant reads the same data you can see, using your permissions, and cannot change anything."
      />
      <Card>
        <div className="flex min-h-[50vh] flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto" role="log" aria-live="polite" aria-label="Conversation">
            {msgs.length === 0 && (
              <div className="space-y-3">
                <p className="text-sm text-muted">Try one of these:</p>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((s) => (
                    <Button key={s} variant="secondary" onClick={() => void send(s)}>{s}</Button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div className={m.role === "user" ? "max-w-[80%] rounded-xl bg-primary/15 px-4 py-2 text-sm text-ink" : "max-w-[85%] rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-ink"}>
                  <div className="whitespace-pre-wrap">{m.content}</div>
                  {m.meta && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
                      <Badge tone={m.meta.mode === "claude" ? "ai" : "default"}>{m.meta.mode === "claude" ? "Claude" : "Built-in summary"}</Badge>
                      {m.meta.tool_calls.length > 0 && <span>Data used: {m.meta.tool_calls.map((t) => t.tool.replace(/_/g, " ")).join(", ")}</span>}
                      {m.meta.notice && <span>{m.meta.notice}</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && <p className="text-sm text-muted" role="status">Thinking…</p>}
            <div ref={endRef} />
          </div>
          {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
          <form onSubmit={submit} className="mt-4 flex gap-2">
            <label htmlFor="chat" className="sr-only">Message</label>
            <textarea
              id="chat"
              rows={2}
              maxLength={MAX_LEN}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(text);
                }
              }}
              placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
              className="flex-1 resize-none rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-primary focus:outline-none"
            />
            <Button type="submit" variant="primary" loading={busy} disabled={!text.trim()}>Send</Button>
          </form>
          <div className="mt-2">
            <Notice>Answers are generated from your data and may be wrong. Check figures on the relevant page before acting.</Notice>
          </div>
        </div>
      </Card>
    </>
  );
}
