"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { ProductPicker } from "@/components/ProductPicker";
import { Button, ErrorState, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtLift, promotionBody, type PromotionForm as Form } from "@/lib/promotions";
import type { HierarchyCategory, PromotionDetail, PromotionEstimate } from "@/lib/types";

const EMPTY: Form = { name: "", start_date: "", end_date: "", scope: "products", product_ids: [""], category: "", discount_pct: "", uplift: "", notes: "" };

/** Plan a promotion. Leave the uplift blank to use the estimate from past promotions. */
export function PromotionForm({ onSaved, onCancel }: { onSaved: (p: PromotionDetail) => void | Promise<void>; onCancel: () => void }) {
  const cats = useSWR<HierarchyCategory[]>("/forecast/hierarchy/categories");
  const [f, setF] = useState<Form>(EMPTY);
  const [est, setEst] = useState<PromotionEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const set = (patch: Partial<Form>) => {
    setF({ ...f, ...patch });
    if ("product_ids" in patch || "category" in patch || "scope" in patch || "discount_pct" in patch) setEst(null);
  };

  const suggest = async () => {
    const ids = f.product_ids.filter(Boolean);
    setErr(null);
    try {
      setEst(
        await api<PromotionEstimate>("/promotions/estimate", {
          method: "POST",
          body: {
            product_ids: f.scope === "products" ? ids : [],
            category: f.scope === "category" ? f.category || null : null,
            discount_pct: f.discount_pct.trim() ? Number(f.discount_pct) : null,
          },
        }),
      );
    } catch (e) {
      setErr(e);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const built = promotionBody(f);
    if ("error" in built) return setErr(new Error(built.error));
    setErr(null);
    setBusy(true);
    try {
      await onSaved(await api<PromotionDetail>("/promotions", { method: "POST", body: built.body }));
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Name" htmlFor="pr-name">
          <Input id="pr-name" maxLength={200} value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="First day" htmlFor="pr-start">
          <Input id="pr-start" type="date" value={f.start_date} onChange={(e) => set({ start_date: e.target.value })} />
        </Field>
        <Field label="Last day" htmlFor="pr-end">
          <Input id="pr-end" type="date" value={f.end_date} onChange={(e) => set({ end_date: e.target.value })} />
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="On promotion" htmlFor="pr-scope">
          <Select id="pr-scope" value={f.scope} onChange={(e) => set({ scope: e.target.value as Form["scope"] })}>
            <option value="products">Chosen products</option>
            <option value="category">A whole category</option>
          </Select>
        </Field>
        <Field label="Discount (%)" htmlFor="pr-disc" hint="Optional; similar past discounts are preferred">
          <Input id="pr-disc" type="number" min={0} max={95} step={1} value={f.discount_pct} onChange={(e) => set({ discount_pct: e.target.value })} />
        </Field>
        <Field label="Expected uplift (×)" htmlFor="pr-uplift" hint="Blank = estimate from past promotions">
          <Input id="pr-uplift" type="number" min={0.1} max={10} step={0.1} value={f.uplift} onChange={(e) => set({ uplift: e.target.value })} />
        </Field>
      </div>
      {f.scope === "category" ? (
        <Field label="Category" htmlFor="pr-cat">
          <Select id="pr-cat" value={f.category} onChange={(e) => set({ category: e.target.value })}>
            <option value="">Choose a category</option>
            {(cats.data ?? []).filter((c) => c.category).map((c) => (
              <option key={c.category} value={c.category!}>{c.label} ({c.products})</option>
            ))}
          </Select>
        </Field>
      ) : (
        <fieldset className="space-y-2">
          <legend className="text-sm text-muted">Products</legend>
          {f.product_ids.map((v, i) => (
            <div key={i} className="flex gap-2">
              <label className="sr-only" htmlFor={`pr-p${i}`}>Product {i + 1}</label>
              <ProductPicker id={`pr-p${i}`} value={v} onChange={(id) => set({ product_ids: f.product_ids.map((x, j) => (j === i ? id : x)) })} />
              {f.product_ids.length > 1 && (
                <Button variant="ghost" onClick={() => set({ product_ids: f.product_ids.filter((_, j) => j !== i) })}>Remove</Button>
              )}
            </div>
          ))}
          <Button variant="ghost" onClick={() => set({ product_ids: [...f.product_ids, ""] })}>Add a product</Button>
        </fieldset>
      )}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Button variant="secondary" onClick={suggest}>Estimate uplift</Button>
        {est && (
          <span className="text-muted" role="status">
            {est.uplift == null ? `No estimate: ${est.basis}.` : `${fmtLift(est.uplift)} from ${est.basis} (${est.promotions} promotion${est.promotions === 1 ? "" : "s"}).`}
          </span>
        )}
      </div>
      <Field label="Notes" htmlFor="pr-notes">
        <Input id="pr-notes" maxLength={500} value={f.notes} onChange={(e) => set({ notes: e.target.value })} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" loading={busy}>Create promotion</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
      {err != null && <ErrorState error={err} />}
    </form>
  );
}
