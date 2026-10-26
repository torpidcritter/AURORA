"use client";

import { useId, useState, type FormEvent } from "react";
import useSWR from "swr";

import { TERMINAL, useJob } from "@/components/JobProgress";
import { Button, ErrorState, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Job, Page } from "@/lib/types";

/** Mirrors TrainIn in aurora-backend/app/api/routes/ops.py. The API validates every field again. */
export const TRAIN_HORIZONS = [7, 14, 28, 56] as const;
export type TrainHorizon = (typeof TRAIN_HORIZONS)[number];
export interface TrainRequest {
  horizon: TrainHorizon;
  max_epochs: number;
  auto_promote: boolean;
}

export function parseEpochs(raw: string): number | null {
  const n = Number(raw.trim());
  return raw.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 200 ? n : null;
}

/** Queues a training job (`POST /train`, Analyst+). A fresh idempotency key per click stops a retried request from
 *  queuing a second job. */
export function startTraining(body: TrainRequest): Promise<Job> {
  return api<Job>("/train", { method: "POST", body, headers: { "idempotency-key": crypto.randomUUID() } });
}

/** The training job to show: the one just started, or one still queued/running from earlier (e.g. after a reload or
 *  when someone else started it). Polls until it finishes. */
export function useTrainingJob(startedId: string | null, enabled = true) {
  const recent = useSWR<Page<Job>>(enabled ? "/jobs?kind=train&limit=1" : null);
  const latest = recent.data?.items[0];
  const trackedId = enabled ? (startedId ?? (latest && !TERMINAL.has(latest.status) ? latest.id : null)) : null;
  const job = useJob(trackedId, (id) => `/train/${id}/status`);
  return { job, refreshRecent: () => recent.mutate() };
}

export function TrainForm({ onStarted, running, onError }: { onStarted: (job: Job) => void; running: boolean; onError?: (e: unknown) => void }) {
  const id = useId();
  const [horizon, setHorizon] = useState<TrainHorizon>(7);
  const [epochs, setEpochs] = useState("30");
  const [autoPromote, setAutoPromote] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const n = parseEpochs(epochs);
    if (n == null) return setErr(new Error("Epochs must be a whole number from 1 to 200"));
    setBusy(true);
    try {
      onStarted(await startTraining({ horizon, max_epochs: n, auto_promote: autoPromote }));
    } catch (e2) {
      setErr(e2);
      onError?.(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <Field label="Horizon (days)" htmlFor={`${id}-h`}>
          <Select id={`${id}-h`} value={horizon} onChange={(e) => setHorizon(Number(e.target.value) as TrainHorizon)}>
            {TRAIN_HORIZONS.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Max epochs" htmlFor={`${id}-e`} hint="Early stopping may end sooner">
          <Input id={`${id}-e`} inputMode="numeric" value={epochs} onChange={(e) => setEpochs(e.target.value)} className="w-28" />
        </Field>
        <label className="mb-2 flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={autoPromote} onChange={(e) => setAutoPromote(e.target.checked)} />
          Promote automatically if the gate passes
        </label>
        <Button type="submit" variant="primary" loading={busy} disabled={running}>
          Start training
        </Button>
      </form>
      {err != null && (
        <div className="mt-3">
          <ErrorState error={err} />
        </div>
      )}
    </>
  );
}
