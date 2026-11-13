"use client";

import { useRouter } from "next/navigation";

import { RiskScatter } from "@/components/charts";
import { Card, EmptyState, ErrorState, LoadingBlock, PageHeader, Stat } from "@/components/ui";
import { useRiskMatrix } from "@/lib/hooks";

export default function RiskPage() {
  const router = useRouter();
  const risk = useRiskMatrix();
  const items = risk.data?.items ?? [];
  const count = (l: string) => items.filter((r) => r.risk_level === l).length;

  return (
    <>
      <PageHeader
        title="Stockout risk"
        description="Each dot is a product with known stock. Further left means fewer days of stock; higher means a larger chance of running out before a new order could arrive."
      />
      {risk.error ? (
        <ErrorState error={risk.error} onRetry={() => risk.mutate()} />
      ) : !risk.data ? (
        <LoadingBlock rows={8} label="Scoring products" />
      ) : items.length === 0 ? (
        <EmptyState title="No products to score" />
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Critical" value={count("critical")} tone={count("critical") ? "bad" : "default"} />
            <Stat label="High" value={count("high")} tone={count("high") ? "warn" : "default"} />
            <Stat label="Medium" value={count("medium")} />
            <Stat label="Low" value={count("low")} tone="ok" />
            <Stat label="Unknown stock" value={count("unknown")} hint="no on-hand count" />
          </div>
          <Card title="Risk matrix" subtitle="Select a dot to open the product. Use the table toggle for an accessible list.">
            <RiskScatter rows={items} onSelect={(id) => router.push(`/products/${id}`)} />
          </Card>
          <p className="mt-3 text-xs text-faint">
            Urgency = 40% time-to-stockout score + 35% stockout-probability score + 25% inventory-position score. Probability uses a normal approximation of lead-time demand; open orders are excluded because their arrival date is unknown.
          </p>
        </>
      )}
    </>
  );
}
