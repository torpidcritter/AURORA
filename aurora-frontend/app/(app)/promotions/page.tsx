"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { PromotionForm } from "@/components/PromotionForm";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingBlock, PageHeader, Table, Td, Th } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { fmtLift, STATUS_TONE } from "@/lib/promotions";
import { useSession } from "@/lib/session";
import type { Promotion } from "@/lib/types";

export default function PromotionsPage() {
  const list = useSWR<Promotion[]>("/promotions");
  const { can } = useSession();
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  const editable = can("manage_overrides");

  return (
    <>
      <PageHeader
        title="Promotions"
        description="Past promotions are measured against the four weeks before them. Planned ones raise the demand used for reorder decisions on their dates."
        actions={editable && !adding ? <Button onClick={() => setAdding(true)}>Plan a promotion</Button> : undefined}
      />
      {adding && (
        <Card title="New promotion" subtitle="A planner override on the same product and day takes precedence over the promotion.">
          <PromotionForm
            onCancel={() => setAdding(false)}
            onSaved={async (p) => {
              setAdding(false);
              await list.mutate();
              router.push(`/promotions/${p.id}`);
            }}
          />
        </Card>
      )}
      <Card>
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={4} label="Loading promotions" />
        ) : list.data.length === 0 ? (
          <EmptyState title="No promotions yet">Record past promotions to learn their lift, and plan upcoming ones so orders cover them.</EmptyState>
        ) : (
          <Table caption="Promotions">
            <thead>
              <tr>
                <Th>Promotion</Th>
                <Th>Dates</Th>
                <Th className="text-right">Products</Th>
                <Th className="text-right">Discount</Th>
                <Th className="text-right">Uplift used</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((p) => (
                <tr key={p.id}>
                  <Td>
                    <Link href={`/promotions/${p.id}`} className="text-primary hover:underline">{p.name}</Link>
                    {p.category && <div className="text-xs text-faint">Category: {p.category}</div>}
                  </Td>
                  <Td>{fmtDate(p.start_date)} – {fmtDate(p.end_date)}</Td>
                  <Td className="text-right tabular-nums">{p.product_count}</Td>
                  <Td className="text-right tabular-nums">{p.discount_pct == null ? "—" : `${p.discount_pct}%`}</Td>
                  <Td className="text-right tabular-nums">{p.status === "finished" ? "—" : fmtLift(p.uplift)}</Td>
                  <Td><Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
