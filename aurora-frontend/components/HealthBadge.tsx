"use client";

import { Activity, AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import { useMemo } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui";
import { buildHealthIndex, type HealthLevel, type ProductHealth } from "@/lib/health";
import type { DriftRow, LiveAccuracy } from "@/lib/types";

/** Loads live accuracy and drift once (SWR shares them with other pages) and indexes health by product id. */
export function useProductHealth() {
  const acc = useSWR<LiveAccuracy>("/intelligence/accuracy");
  const drift = useSWR<{ items: DriftRow[]; drifting: number }>("/intelligence/drift");
  const index = useMemo(() => (acc.data && drift.data ? buildHealthIndex(acc.data, drift.data) : null), [acc.data, drift.data]);
  const error: unknown = acc.error ?? drift.error ?? null;
  return {
    index,
    error,
    loading: !index && error == null,
    retry: () => {
      void acc.mutate();
      void drift.mutate();
    },
  };
}

const ICONS: Record<HealthLevel, typeof Activity> = {
  off_target: AlertTriangle,
  drifting: Activity,
  healthy: CheckCircle2,
  insufficient: HelpCircle,
};

/** Compact status badge: icon + label + colour, with the explanation as a tooltip and as screen-reader text. */
export function HealthBadge({ health }: { health: ProductHealth }) {
  const Icon = ICONS[health.level];
  return (
    <span title={health.summary} className="inline-flex cursor-help">
      <Badge tone={health.tone}>
        <Icon className="h-3 w-3" aria-hidden />
        {health.label}
      </Badge>
      <span className="sr-only">. {health.summary}</span>
    </span>
  );
}
