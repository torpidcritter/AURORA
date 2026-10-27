"use client";

import { CheckCircle2, Circle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Button, Card } from "@/components/ui";
import type { Onboarding } from "@/lib/types";

const DISMISS_KEY = "aurora.onboarding.dismissed";

function readDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

/** Getting-started checklist on the dashboard. Hidden once everything is done or the viewer hides it. */
export function GettingStarted() {
  const data = useSWR<Onboarding>("/onboarding");
  // Nothing renders before the checklist loads (never on the server), so reading storage here cannot cause a mismatch.
  const [dismissed, setDismissed] = useState(readDismissed);
  const o = data.data;
  if (!o || o.complete || dismissed) return null;

  const hide = () => {
    try {
      window.localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // storage blocked: hide for this visit only
    }
    setDismissed(true);
  };
  return (
    <Card
      title="Getting started"
      subtitle={`${o.done} of ${o.total} done`}
      className="mb-6"
      actions={<Button variant="ghost" onClick={hide}>Hide</Button>}
    >
      <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label="Setup progress" aria-valuemin={0} aria-valuemax={o.total} aria-valuenow={o.done}>
        <div className="h-full bg-primary" style={{ width: `${(o.done / o.total) * 100}%` }} />
      </div>
      <ol className="grid gap-2 sm:grid-cols-2">
        {o.steps.map((s) => (
          <li key={s.key} className="flex gap-2 text-sm">
            {s.done ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-label="Done" /> : <Circle className="mt-0.5 h-4 w-4 shrink-0 text-faint" aria-label="To do" />}
            <div>
              {s.done ? <span className="text-muted line-through">{s.title}</span> : <Link href={s.href} className="text-ink hover:text-primary hover:underline">{s.title}</Link>}
              {!s.done && <div className="text-xs text-faint">{s.detail}</div>}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
