"use client";

import { ApiKeysCard, WebhooksCard } from "@/components/Developers";
import { Notice, PageHeader } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function DevelopersPage() {
  const { can } = useSession();
  if (!can("manage_integrations")) return <Notice tone="warn">Only administrators can manage API keys and webhooks.</Notice>;
  return (
    <>
      <PageHeader
        title="API and webhooks"
        description="Connect other systems: API keys let them read and change data through Aurora's API; webhooks tell them when something happens."
      />
      <ApiKeysCard />
      <WebhooksCard />
    </>
  );
}
