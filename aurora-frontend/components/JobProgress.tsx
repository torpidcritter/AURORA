"use client";

import useSWR from "swr";

import { Badge, Button, ErrorState, ProgressBar } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { Job } from "@/lib/types";

export const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

/** Polls a job every 2 s until it reaches a terminal state. */
export function useJob(jobId: string | null, path: (id: string) => string = (id) => `/jobs/${id}`) {
  return useSWR<Job>(jobId ? path(jobId) : null, {
    refreshInterval: (j) => (j && TERMINAL.has(j.status) ? 0 : 2000),
  });
}

const tone = (s: Job["status"]) => (s === "succeeded" ? "ok" : s === "failed" ? "bad" : s === "cancelled" ? "warn" : "info");

export function JobProgress({ job, canCancel, onChanged }: { job: Job; canCancel?: boolean; onChanged?: () => void }) {
  const active = !TERMINAL.has(job.status);
  return (
    <div className="space-y-2 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={tone(job.status)}>{job.status}</Badge>
        <span className="text-muted">{job.kind.replace(/_/g, " ")} · started {fmtDateTime(job.started_at ?? job.created_at)}</span>
        {job.phase && <span className="text-muted">· {job.phase}</span>}
        {job.epoch != null && (
          <span className="text-muted">
            · epoch {job.epoch}, train loss {job.loss?.toFixed(4)}, validation loss {job.val_loss?.toFixed(4)}
          </span>
        )}
        {active && canCancel && !job.cancel_requested && (
          <Button
            variant="ghost"
            className="ml-auto"
            onClick={async () => {
              await api(`/jobs/${job.id}/cancel`, { method: "POST" });
              onChanged?.();
            }}
          >
            Cancel
          </Button>
        )}
        {job.cancel_requested && active && <span className="ml-auto text-xs text-warn">Cancelling…</span>}
      </div>
      {active && <ProgressBar value={job.progress} label={`${job.kind} progress`} />}
      {job.status === "failed" && job.error && <ErrorState error={new Error(job.error)} />}
    </div>
  );
}
