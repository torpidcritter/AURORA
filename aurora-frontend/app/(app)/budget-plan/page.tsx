"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { BudgetPlanView } from "@/components/BudgetPlanView";
import { Button, Card, ErrorState, Field, Input, Notice, PageHeader, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { budgetErrors, parseBudget, planQuantities } from "@/lib/budget";
import { useSession } from "@/lib/session";
import type { BudgetPlan, PurchaseOrder } from "@/lib/types";

export default function BudgetPlanPage() {
  const { can } = useSession();
  const cats = useSWR<string[]>("/products/categories");
  const [budget, setBudget] = useState("");
  const [policy, setPolicy] = useState<"service" | "urgency">("service");
  const [category, setCategory] = useState("");
  const [plan, setPlan] = useState<BudgetPlan | null>(null);
  const [drafted, setDrafted] = useState<PurchaseOrder[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setDrafted(null);
    const problem = budgetErrors(budget);
    if (problem) return setErr(new Error(problem));
    setBusy("plan");
    try {
      setPlan(await api<BudgetPlan>("/purchase-orders/budget-plan", { method: "POST", body: { budget: parseBudget(budget), policy, category: category || null } }));
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(null);
    }
  };

  const draft = async () => {
    if (!plan) return;
    setErr(null);
    setBusy("draft");
    try {
      const r = await api<{ created: PurchaseOrder[] }>("/purchase-orders/suggest", { method: "POST", body: { quantities: planQuantities(plan) } });
      setDrafted(r.created);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Order on a budget"
        description="When cash is limited, find the mix of reorders that keeps the most demand served for the money. Nothing is ordered until you draft purchase orders and approve them."
      />
      <Card className="mb-6">
        <form onSubmit={run} aria-label="Budget plan" className="grid gap-3 md:grid-cols-4">
          <Field label="Budget" htmlFor="bb"><Input id="bb" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="e.g. 5000" /></Field>
          <Field label="Spend first on" htmlFor="bp">
            <Select id="bp" value={policy} onChange={(e) => setPolicy(e.target.value as "service" | "urgency")}>
              <option value="service">Most demand served per unit spent</option>
              <option value="urgency">Highest stockout risk</option>
            </Select>
          </Field>
          <Field label="Products" htmlFor="bc">
            <Select id="bc" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {(cats.data ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
          </Field>
          <div className="flex items-end"><Button type="submit" variant="primary" loading={busy === "plan"}>Plan orders</Button></div>
        </form>
        {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      </Card>
      {plan && (
        <Card
          title="Plan"
          actions={
            can("manage_purchasing") && plan.products_funded > 0 && !drafted ? (
              <Button variant="primary" loading={busy === "draft"} onClick={() => void draft()}>Draft purchase orders</Button>
            ) : undefined
          }
        >
          {drafted && (
            <div className="mb-4">
              <Notice tone="ok">
                Drafted {drafted.length} purchase order{drafted.length === 1 ? "" : "s"}.{" "}
                <Link className="text-primary hover:underline" href="/purchase-orders">Review them</Link> before approving.
              </Notice>
            </div>
          )}
          <BudgetPlanView plan={plan} />
        </Card>
      )}
    </>
  );
}
