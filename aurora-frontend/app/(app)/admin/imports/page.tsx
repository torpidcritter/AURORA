"use client";

import Link from "next/link";

import { ImportSourcesCard, NewImportCard } from "@/components/Imports";
import { Notice, PageHeader } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function ImportsPage() {
  const { can } = useSession();
  if (!can("manage_integrations")) return <Notice tone="warn">Only administrators can manage scheduled imports.</Notice>;
  return (
    <>
      <PageHeader
        title="Data imports"
        description={
          <>
            Bring in sales automatically from a file at a web address or a WooCommerce store. Shopify connects under{" "}
            <Link href="/admin/ops" className="text-primary hover:underline">System &amp; integrations</Link>; one-off files go through{" "}
            <Link href="/upload" className="text-primary hover:underline">Upload data</Link>.
          </>
        }
      />
      <div className="space-y-6">
        <ImportSourcesCard />
        <NewImportCard />
      </div>
    </>
  );
}
