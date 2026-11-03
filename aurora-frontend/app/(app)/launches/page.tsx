"use client";

import Link from "next/link";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, LoadingBlock, PageHeader, Table, Td, Th } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { LaunchRow } from "@/lib/types";

export default function LaunchesPage() {
  const list = useSWR<LaunchRow[]>("/launches");
  return (
    <>
      <PageHeader
        title="Launches"
        description="New products whose demand is planned from similar products. Add a plan from a product's page; it is used until the product has about 8 weeks of its own sales."
      />
      <Card>
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={4} label="Loading launches" />
        ) : list.data.length === 0 ? (
          <EmptyState title="No launch plans yet">Open a new product and choose “Plan a launch”.</EmptyState>
        ) : (
          <Table caption="Launch plans">
            <thead>
              <tr>
                <Th>Product</Th>
                <Th>Launch date</Th>
                <Th className="text-right">Expected vs. similar</Th>
                <Th className="text-right">Days of own sales</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((l) => (
                <tr key={l.id}>
                  <Td>
                    <Link href={`/products/${l.product_id}`} className="text-primary hover:underline">{l.sku}</Link>
                    <div className="text-xs text-faint">{l.name}</div>
                  </Td>
                  <Td>{fmtDate(l.launch_date)}</Td>
                  <Td className="text-right tabular-nums">{Math.round(l.scale * 100)}%</Td>
                  <Td className="text-right tabular-nums">{l.own_days}</Td>
                  <Td>{l.active ? <Badge tone="info">plan in use</Badge> : <Badge>own history</Badge>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
