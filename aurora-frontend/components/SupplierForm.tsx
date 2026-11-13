"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorState, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { Supplier } from "@/lib/types";

interface Values {
  name: string;
  contact_name: string;
  email: string;
  phone: string;
  lead_time_days: string;
  min_order_value: string;
  notes: string;
}

const EMPTY: Values = { name: "", contact_name: "", email: "", phone: "", lead_time_days: "", min_order_value: "", notes: "" };

function fromSupplier(s: Supplier): Values {
  return {
    name: s.name,
    contact_name: s.contact_name ?? "",
    email: s.email ?? "",
    phone: s.phone ?? "",
    lead_time_days: s.lead_time_days == null ? "" : String(s.lead_time_days),
    min_order_value: s.min_order_value == null ? "" : String(s.min_order_value),
    notes: s.notes ?? "",
  };
}

/** Turn form text into an API body. Blank optional fields become null. */
export function supplierBody(v: Values): { body?: Record<string, unknown>; error?: string } {
  if (!v.name.trim()) return { error: "Name is required" };
  const lead = v.lead_time_days.trim();
  if (lead && (!/^\d+$/.test(lead) || Number(lead) < 1 || Number(lead) > 365)) return { error: "Lead time must be a whole number of days from 1 to 365" };
  const mov = v.min_order_value.trim();
  if (mov && (!Number.isFinite(Number(mov)) || Number(mov) < 0)) return { error: "Minimum order value must be a number of 0 or more" };
  const email = v.email.trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { error: "Enter a valid e-mail address" };
  const opt = (s: string) => (s.trim() ? s.trim() : null);
  return {
    body: {
      name: v.name.trim(),
      contact_name: opt(v.contact_name),
      email: opt(v.email),
      phone: opt(v.phone),
      lead_time_days: lead ? Number(lead) : null,
      min_order_value: mov ? Number(mov) : null,
      notes: opt(v.notes),
    },
  };
}

export function SupplierForm({ supplier, onSaved, onCancel }: { supplier?: Supplier; onSaved: (s: Supplier) => void | Promise<void>; onCancel?: () => void }) {
  const [v, setV] = useState<Values>(supplier ? fromSupplier(supplier) : EMPTY);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Values) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  const id = supplier?.id ?? "new";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const { body, error } = supplierBody(v);
    if (error) return setErr(new Error(error));
    setBusy(true);
    try {
      const saved = supplier
        ? await api<Supplier>(`/suppliers/${supplier.id}`, { method: "PATCH", body: { ...body, expected_version: supplier.version } })
        : await api<Supplier>("/suppliers", { method: "POST", body });
      if (!supplier) setV(EMPTY);
      await onSaved(saved);
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-3 md:grid-cols-3" aria-label={supplier ? `Edit ${supplier.name}` : "New supplier"}>
      <Field label="Name" htmlFor={`${id}-n`}><Input id={`${id}-n`} value={v.name} onChange={set("name")} maxLength={200} required /></Field>
      <Field label="Contact" htmlFor={`${id}-c`}><Input id={`${id}-c`} value={v.contact_name} onChange={set("contact_name")} maxLength={200} /></Field>
      <Field label="E-mail" htmlFor={`${id}-e`}><Input id={`${id}-e`} type="email" value={v.email} onChange={set("email")} maxLength={320} /></Field>
      <Field label="Phone" htmlFor={`${id}-p`}><Input id={`${id}-p`} value={v.phone} onChange={set("phone")} maxLength={50} /></Field>
      <Field label="Lead time (days)" htmlFor={`${id}-l`} hint="Used for products without their own lead time">
        <Input id={`${id}-l`} inputMode="numeric" value={v.lead_time_days} onChange={set("lead_time_days")} />
      </Field>
      <Field label="Minimum order value" htmlFor={`${id}-m`} hint="Orders below it get a warning">
        <Input id={`${id}-m`} inputMode="decimal" value={v.min_order_value} onChange={set("min_order_value")} />
      </Field>
      <Field label="Notes" htmlFor={`${id}-o`}><Input id={`${id}-o`} value={v.notes} onChange={set("notes")} maxLength={2000} /></Field>
      <div className="flex items-end gap-2 md:col-span-2">
        <Button type="submit" variant="primary" loading={busy}>{supplier ? "Save" : "Add supplier"}</Button>
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
      {err != null && <div className="md:col-span-3"><ErrorState error={err} /></div>}
    </form>
  );
}
