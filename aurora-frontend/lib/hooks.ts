"use client";

import useSWR from "swr";

import { api } from "@/lib/api";
import type { Recommendation } from "@/lib/types";

/** Risk matrix for every product (POST, read-only on the server). Cached under a synthetic SWR key. */
export function useRiskMatrix() {
  return useSWR<{ items: Recommendation[]; total: number }>("risk-matrix", () =>
    api<{ items: Recommendation[]; total: number }>("/risk/stockout/batch", { method: "POST", body: {} }),
  );
}

export const RISK_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, unknown: 4 };
