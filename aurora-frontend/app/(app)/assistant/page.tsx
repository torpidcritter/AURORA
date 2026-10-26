"use client";

import { ChatComposer, ChatDisclaimer, ChatLog, useChat } from "@/components/chat";
import { Card, ErrorState, PageHeader } from "@/components/ui";

export default function AssistantPage() {
  // The same conversation as the floating assistant panel (see components/chat.tsx).
  const { msgs, busy, error, send } = useChat();

  return (
    <>
      <PageHeader
        title="Assistant"
        description="Ask about your products, risk, alerts and drift. The assistant reads the same data you can see, using your permissions, and cannot change anything."
      />
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
