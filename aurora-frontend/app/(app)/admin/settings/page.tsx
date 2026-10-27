"use client";

import useSWR from "swr";

import { DataExportCard, OrgDeletionCard } from "@/components/DataRights";
import { OrgSettingsForm } from "@/components/OrgSettingsForm";
import { ErrorState, LoadingBlock, PageHeader } from "@/components/ui";
import { useSession } from "@/lib/session";
import type { OrgSettings } from "@/lib/types";

export default function SettingsPage() {
  const { can, loading } = useSession();
  const s = useSWR<OrgSettings>("/settings/org");
  return (
    <>
      <PageHeader
        title="Settings"
        description="Organisation defaults for inventory recommendations, monitoring thresholds and alert notifications. A setting left at its default follows the platform default; an override applies to this organisation only."
      />
      {s.error ? (
        <ErrorState error={s.error} onRetry={() => s.mutate()} />
      ) : !s.data || loading ? (
        <LoadingBlock rows={8} label="Loading settings" />
      ) : (
        <OrgSettingsForm data={s.data} canEdit={can("manage_settings")} onSaved={(updated) => s.mutate(updated, { revalidate: false })} />
      )}
      {can("manage_settings") && (
        <div className="mt-6 grid gap-6 xl:grid-cols-2">
          <DataExportCard />
          <OrgDeletionCard />
        </div>
      )}
    </>
  );
}
