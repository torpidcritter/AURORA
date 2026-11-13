"use client";

import { use } from "react";

import { PurchaseOrderView } from "@/components/PurchaseOrderView";

export default function PurchaseOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <PurchaseOrderView id={id} />;
}
