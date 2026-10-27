"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { ProductPicker } from "@/components/ProductPicker";
import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtInt, fmtMoney } from "@/lib/format";
import { availableActions, linesPayload, PO_STATUS_LABEL, PO_STATUS_TONE, receivePayload, toDraftLines, type DraftLine } from "@/lib/purchasing";
import { useSession } from "@/lib/session";
import type { Page, Product, PurchaseOrder, Supplier } from "@/lib/types";

export function PurchaseOrderView({ id }: { id: string }) {
  const router = useRouter();
  const { can } = useSession();
  const order = useSWR<PurchaseOrder>(`/purchase-orders/${id}`);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [receiving, setReceiving] = useState(false);

  if (order.error) return <ErrorState error={order.error} onRetry={() => order.mutate()} />;
  const po = order.data;
  if (!po) return <LoadingBlock rows={8} label="Loading order" />;
  const actions = availableActions(po.status, can);

  const act = async (action: "approve" | "send" | "cancel", body: Record<string, unknown> = {}) => {
    setErr(null);
    setBusy(action);
    try {
      const updated = await api<PurchaseOrder>(`/purchase-orders/${po.id}/${action}`, { method: "POST", body: { expected_version: po.version, ...body } });
      await order.mutate(updated, { revalidate: false });
    } catch (e) {
      setErr(e);
      await order.mutate();
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="mb-2 text-sm"><Link href="/purchase-orders" className="text-primary hover:underline">Purchase orders</Link> / {po.number}</div>
      <PageHeader
        title={`${po.number} · ${po.supplier_name ?? "No supplier"}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone={PO_STATUS_TONE[po.status]}>{PO_STATUS_LABEL[po.status]}</Badge>
            {po.source === "suggested" && <Badge tone="ai">Drafted from suggestions</Badge>}
            <span>Created {fmtDateTime(po.created_at)}</span>
            {po.expected_date && <span>· expected {fmtDate(po.expected_date)}</span>}
          </span>
        }
        actions={
          <>
            <a className="text-sm text-primary hover:underline" href={`/api/proxy/purchase-orders/${po.id}/export.csv`} download>
              Download CSV
            </a>
            {actions.includes("edit") && !editing && <Button onClick={() => setEditing(true)}>Edit</Button>}
            {actions.includes("approve") && (
              <Button variant="primary" loading={busy === "approve"} disabled={editing || busy != null} onClick={() => void act("approve")}>Approve</Button>
            )}
            {actions.includes("send") && (
              <ConfirmButton
                variant="primary"
                title={`Mark ${po.number} as sent?`}
                body="Aurora does not e-mail the supplier. Send them the CSV yourself; marking it sent adds these quantities to stock on order."
                confirmLabel="Mark as sent"
                onConfirm={() => act("send")}
              >
                Mark as sent
              </ConfirmButton>
            )}
            {actions.includes("receive") && !receiving && <Button variant="primary" onClick={() => setReceiving(true)}>Receive goods</Button>}
            {actions.includes("cancel") && (
              <ConfirmButton
                title={`Cancel ${po.number}?`}
                body={po.status === "approved" ? "The order will be closed without being sent." : "Quantities not yet received are removed from stock on order. Goods already received stay in stock."}
                confirmLabel="Cancel order"
                onConfirm={() => act("cancel")}
              >
                Cancel order
              </ConfirmButton>
            )}
            {actions.includes("delete") && (
              <ConfirmButton
                title={`Delete draft ${po.number}?`}
                body="The draft and its lines are removed. This cannot be undone."
                confirmLabel="Delete"
                onConfirm={async () => {
                  await api(`/purchase-orders/${po.id}?expected_version=${po.version}`, { method: "DELETE" });
                  router.push("/purchase-orders");
                }}
              >
                Delete
              </ConfirmButton>
            )}
          </>
        }
      />
      {err != null && <div className="mb-4"><ErrorState error={err} /></div>}
      {po.warnings.length > 0 && (
        <div className="mb-4 space-y-2">
          {po.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
        </div>
      )}
      {po.status === "draft" && !po.supplier_id && !editing && (
        <div className="mb-4"><Notice tone="info">Choose a supplier before this order can be approved.</Notice></div>
      )}

      {editing ? (
        <DraftEditor
          po={po}
          onCancel={() => setEditing(false)}
          onSaved={async (updated) => {
            setEditing(false);
            await order.mutate(updated, { revalidate: false });
          }}
        />
      ) : receiving ? (
        <ReceiveForm
          po={po}
          onCancel={() => setReceiving(false)}
          onSaved={async (updated) => {
            setReceiving(false);
            await order.mutate(updated, { revalidate: false });
          }}
        />
      ) : (
        <Card title="Lines" subtitle={`${fmtInt(po.total_units)} ${po.total_units === 1 ? "unit" : "units"} · ${fmtMoney(po.total_value)}${po.total_complete ? "" : " (some costs missing)"}`}>
          {po.lines.length === 0 ? (
            <EmptyState title="No lines yet">{actions.includes("edit") ? "Use Edit to add products." : null}</EmptyState>
          ) : (
            <Table caption={`Lines of ${po.number}`}>
              <thead>
                <tr>
                  <Th>SKU</Th>
                  <Th className="text-right">Quantity</Th>
                  <Th className="text-right">Unit cost</Th>
                  <Th className="text-right">Value</Th>
                  <Th className="text-right">Received</Th>
                  <Th>Why</Th>
                </tr>
              </thead>
              <tbody>
                {po.lines.map((l) => (
                  <tr key={l.id}>
                    <Td>
                      <Link className="font-medium text-primary hover:underline" href={`/products/${l.product_id}`}>{l.sku}</Link>
                      <div className="text-xs text-faint">{l.name}</div>
                    </Td>
                    <Td className="text-right tabular-nums">{fmtInt(l.quantity)}</Td>
                    <Td className="text-right tabular-nums">{fmtMoney(l.unit_cost)}</Td>
                    <Td className="text-right tabular-nums">{fmtMoney(l.line_value)}</Td>
                    <Td className="text-right tabular-nums">{fmtInt(l.received_qty)}</Td>
                    <Td className="text-xs text-muted">{suggestionText(l.suggestion, l.suggested_qty, l.quantity)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          {po.notes && <p className="mt-3 text-sm text-muted">Notes: {po.notes}</p>}
          <p className="mt-3 text-xs text-faint">
            {[
              po.approved_at && `Approved ${fmtDateTime(po.approved_at)}`,
              po.sent_at && `sent ${fmtDateTime(po.sent_at)}`,
              po.received_at && `received ${fmtDateTime(po.received_at)}`,
              po.cancelled_at && `cancelled ${fmtDateTime(po.cancelled_at)}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </Card>
      )}
    </>
  );
}

function suggestionText(s: Record<string, unknown> | null, suggested: number | null, qty: number): string {
  if (!s) return "Added by hand";
  const parts = [`Suggested ${suggested ?? "?"} (risk ${String(s.risk_level ?? "?")})`];
  if (typeof s.days_until_stockout === "number") parts.push(`${s.days_until_stockout.toFixed(1)} days of stock left`);
  if (Number(s.overrides_applied) > 0) parts.push("planner override applied");
  if (suggested != null && suggested !== qty) parts.push(`changed to ${qty}`);
  return parts.join(" · ");
}

function DraftEditor({ po, onSaved, onCancel }: { po: PurchaseOrder; onSaved: (po: PurchaseOrder) => void | Promise<void>; onCancel: () => void }) {
  const suppliers = useSWR<Supplier[]>("/suppliers");
  const products = useSWR<Page<Product>>("/products?limit=500&sort=sku");
  const [supplierId, setSupplierId] = useState(po.supplier_id ?? "");
  const [expected, setExpected] = useState(po.expected_date ?? "");
  const [notes, setNotes] = useState(po.notes ?? "");
  const [lines, setLines] = useState<DraftLine[]>(toDraftLines(po.lines));
  const [pick, setPick] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const add = () => {
    const p = products.data?.items.find((x) => x.id === pick);
    if (!p || lines.some((l) => l.product_id === p.id)) return;
    const qty = Math.max(p.min_order_qty || 1, p.pack_size || 1);
    setLines([...lines, { product_id: p.id, sku: p.sku, name: p.name, quantity: String(qty), unit_cost: p.unit_cost == null ? "" : String(p.unit_cost) }]);
    setPick("");
  };
  const update = (i: number, k: "quantity" | "unit_cost", v: string) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  const save = async () => {
    setErr(null);
    const parsed = linesPayload(lines);
    if (parsed.error) return setErr(new Error(parsed.error));
    setBusy(true);
    try {
      const updated = await api<PurchaseOrder>(`/purchase-orders/${po.id}`, {
        method: "PATCH",
        body: { expected_version: po.version, supplier_id: supplierId || null, expected_date: expected || null, notes: notes.trim() || null, lines: parsed.lines },
      });
      await onSaved(updated);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Edit draft">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Supplier" htmlFor="pos-s">
          <Select id="pos-s" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
            <option value="">No supplier yet</option>
            {(suppliers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
        </Field>
        <Field label="Expected delivery" htmlFor="pos-d" hint="Left blank, it is set from the supplier's lead time when sent">
          <Input id="pos-d" type="date" value={expected} onChange={(e) => setExpected(e.target.value)} />
        </Field>
        <Field label="Notes" htmlFor="pos-n"><Input id="pos-n" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} /></Field>
      </div>
      <div className="mt-4">
        <Table caption="Draft lines">
          <thead>
            <tr>
              <Th>SKU</Th>
              <Th>Quantity</Th>
              <Th>Unit cost</Th>
              <Th><span className="sr-only">Remove</span></Th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={l.product_id}>
                <Td>{l.sku}<div className="text-xs text-faint">{l.name}</div></Td>
                <Td>
                  <label className="sr-only" htmlFor={`q-${l.product_id}`}>Quantity for {l.sku}</label>
                  <Input id={`q-${l.product_id}`} inputMode="numeric" value={l.quantity} onChange={(e) => update(i, "quantity", e.target.value)} className="w-28" />
                </Td>
                <Td>
                  <label className="sr-only" htmlFor={`c-${l.product_id}`}>Unit cost for {l.sku}</label>
                  <Input id={`c-${l.product_id}`} inputMode="decimal" value={l.unit_cost} placeholder="unknown" onChange={(e) => update(i, "unit_cost", e.target.value)} className="w-28" />
                </Td>
                <Td className="text-right"><Button variant="ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</Button></Td>
              </tr>
            ))}
          </tbody>
        </Table>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="Add a product" htmlFor="pos-add"><ProductPicker id="pos-add" value={pick} onChange={setPick} /></Field>
          <Button onClick={add} disabled={!pick}>Add line</Button>
        </div>
      </div>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      <div className="mt-4 flex gap-2">
        <Button variant="primary" loading={busy} onClick={() => void save()}>Save draft</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  );
}

function ReceiveForm({ po, onSaved, onCancel }: { po: PurchaseOrder; onSaved: (po: PurchaseOrder) => void | Promise<void>; onCancel: () => void }) {
  const open = po.lines.filter((l) => l.outstanding_qty > 0);
  const [inputs, setInputs] = useState<Record<string, string>>(Object.fromEntries(open.map((l) => [l.product_id, String(l.outstanding_qty)])));
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setErr(null);
    const parsed = receivePayload(open, inputs);
    if (parsed.error) return setErr(new Error(parsed.error));
    setBusy(true);
    try {
      const updated = await api<PurchaseOrder>(`/purchase-orders/${po.id}/receive`, { method: "POST", body: { expected_version: po.version, lines: parsed.lines } });
      await onSaved(updated);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Receive goods" subtitle="Enter what arrived. Anything left over stays on order and can be received later.">
      <Table caption="Quantities received">
        <thead>
          <tr>
            <Th>SKU</Th>
            <Th className="text-right">Outstanding</Th>
            <Th>Received now</Th>
          </tr>
        </thead>
        <tbody>
          {open.map((l) => (
            <tr key={l.product_id}>
              <Td>{l.sku}<div className="text-xs text-faint">{l.name}</div></Td>
              <Td className="text-right tabular-nums">{fmtInt(l.outstanding_qty)}</Td>
              <Td>
                <label className="sr-only" htmlFor={`r-${l.product_id}`}>Received quantity for {l.sku}</label>
                <Input id={`r-${l.product_id}`} inputMode="numeric" value={inputs[l.product_id] ?? ""} onChange={(e) => setInputs({ ...inputs, [l.product_id]: e.target.value })} className="w-28" />
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      <div className="mt-4 flex gap-2">
        <Button variant="primary" loading={busy} onClick={() => void save()}>Book into stock</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  );
}
