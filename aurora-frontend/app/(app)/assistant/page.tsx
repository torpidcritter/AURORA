"use client";

import useSWR from "swr";

import { ChatComposer, ChatDisclaimer, ChatLog, ProposalCard, useChat } from "@/components/chat";
import { Card, ErrorState, PageHeader } from "@/components/ui";
import type { AssistantProposal } from "@/lib/types";

export default function AssistantPage() {
  // The same conversation as the floating assistant panel (see components/chat.tsx).
  const { msgs, busy, error, send } = useChat();
  // Proposals from earlier conversations that are still waiting (they expire after a day).
  const pending = useSWR<AssistantProposal[]>("/assistant/proposals");
  const inChat = new Set(msgs.flatMap((m) => m.meta?.proposals ?? []).map((p) => p.id));
  const earlier = (pending.data ?? []).filter((p) => !inChat.has(p.id));

  return (
    <>
      <PageHeader
        title="Assistant"
        description="Ask about your products, risk, alerts and drift. The assistant reads the same data you can see, using your permissions. It can propose drafting orders, acknowledging alerts or adding a forecast override, but nothing changes until you confirm."
      />
      {earlier.length > 0 && (
        <Card title="Waiting for your confirmation" subtitle="Changes the assistant proposed earlier. Each expires a day after it was proposed.">
          {earlier.map((p) => (
            <ProposalCard key={p.id} proposal={p} />
          ))}
        </Card>
      )}
      <Card>
        <div className="flex min-h-[50vh] flex-col">
          <ChatLog msgs={msgs} busy={busy} onSuggestion={(s) => void send(s)} />
          {error != null && (
            <div className="mt-3">
              <ErrorState error={error} />
            </div>
          )}
          <ChatComposer id="chat" onSend={send} busy={busy} />
          <div className="mt-2">
            <ChatDisclaimer />
          </div>
        </div>
      </Card>
    </>
  );
}
