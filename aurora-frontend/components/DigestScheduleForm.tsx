"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorState, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { CHANNELS, digestBody, digestErrors, emptyDigest, hh, SECTIONS, WEEKDAYS, type DigestForm } from "@/lib/digests";
import type { DigestChannel, DigestSchedule } from "@/lib/types";

/** New digest schedule (admins). */
export function DigestScheduleForm({ onCreated }: { onCreated: (s: DigestSchedule) => void | Promise<void> }) {
  const [f, setF] = useState<DigestForm>(emptyDigest);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const channel = CHANNELS.find((c) => c.key === f.channel)!;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const problem = digestErrors(f);
    if (problem) return setErr(new Error(problem));
    setBusy(true);
    try {
      const s = await api<DigestSchedule>("/digests/schedules", { method: "POST", body: digestBody(f) });
      setF(emptyDigest());
      await onCreated(s);
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} aria-label="New digest" className="grid gap-3 md:grid-cols-4">
      <Field label="Digest name" htmlFor="dn"><Input id="dn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} maxLength={200} /></Field>
      <Field label="How often" htmlFor="df">
        <Select id="df" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value as DigestForm["frequency"] })}>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </Select>
      </Field>
      {f.frequency === "weekly" ? (
        <Field label="Day" htmlFor="dw">
          <Select id="dw" value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </Select>
        </Field>
      ) : (
        <div className="hidden md:block" />
      )}
      <Field label="Time" htmlFor="dh">
        <Select id="dh" value={f.hour_utc} onChange={(e) => setF({ ...f, hour_utc: Number(e.target.value) })}>
          {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hh(h)}</option>)}
        </Select>
      </Field>
      <fieldset className="md:col-span-4">
        <legend className="mb-1 text-xs font-medium text-muted">Include</legend>
        <div className="flex flex-wrap gap-4">
          {SECTIONS.map((s) => (
            <label key={s.key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={f.sections.includes(s.key)}
                onChange={(e) => setF({ ...f, sections: e.target.checked ? [...f.sections, s.key] : f.sections.filter((x) => x !== s.key) })}
              />
              {s.label}
            </label>
          ))}
        </div>
      </fieldset>
      <Field label="Send to" htmlFor="dc">
        <Select id="dc" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value as DigestChannel, target: "" })}>
          {CHANNELS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </Select>
      </Field>
      {channel.target ? (
        <div className="md:col-span-2">
          <Field label={channel.target} htmlFor="dt" hint={channel.hint}>
            <Input id="dt" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} maxLength={2000} autoComplete="off" />
          </Field>
        </div>
      ) : (
        <div className="hidden md:col-span-2 md:block" />
      )}
      <div className="flex items-end"><Button type="submit" variant="primary" loading={busy}>Create digest</Button></div>
      {err != null && <div className="md:col-span-4"><ErrorState error={err} /></div>}
    </form>
  );
}
