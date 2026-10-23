"use client";

import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, LoadingBlock, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

interface Shop {
  id: string;
  shop_domain: string;
  scopes: string[];
  connected: boolean;
  connected_at: string;
  uninstalled_at: string | null;
}

const SHOP_RE = /^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$/;

/** Admin-only card: connect a Shopify store (OAuth) and see connected stores. */
export function ShopifyCard() {
  const params = useSearchParams();
  const shops = useSWR<{ items: Shop[]; configured: boolean }>("/shopify/shops");
  const [shop, setShop] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const status = params.get("shopify");

  const connect = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const domain = shop.trim().toLowerCase();
    if (!SHOP_RE.test(domain)) return setErr(new Error("Enter your store's address, like your-store.myshopify.com"));
    setBusy(true);
    try {
      const { authorize_url } = await api<{ authorize_url: string }>("/shopify/install", { method: "POST", body: { shop: domain } });
      // Only ever leave the app for the shop's own Shopify admin.
      const u = new URL(authorize_url);
      if (u.protocol !== "https:" || u.host !== domain) throw new Error("Unexpected authorisation address");
      window.location.assign(u.toString());
    } catch (e2) {
      setErr(e2);
      setBusy(false);
    }
  };

  return (
    <Card title="Shopify" subtitle="Orders from connected stores are added to daily sales automatically (source: shopify).">
      {status === "connected" && <div className="mb-3"><Notice tone="ok">Store connected. New orders will appear in sales history.</Notice></div>}
      {status === "error" && <div className="mb-3"><Notice tone="bad">The store could not be connected. Start again from this page; links from Shopify expire after 10 minutes.</Notice></div>}
      {shops.error ? (
        <ErrorState error={shops.error} onRetry={() => shops.mutate()} />
      ) : !shops.data ? (
        <LoadingBlock rows={2} />
      ) : (
        <>
          {!shops.data.configured ? (
            <Notice tone="warn">The Shopify integration is not configured on the server (SHOPIFY_API_KEY / SHOPIFY_API_SECRET).</Notice>
          ) : (
            <form onSubmit={connect} className="flex flex-wrap items-end gap-3">
              <Field label="Store address" htmlFor="shop">
                <Input id="shop" placeholder="your-store.myshopify.com" value={shop} onChange={(e) => setShop(e.target.value)} className="w-72" />
              </Field>
              <Button type="submit" variant="primary" loading={busy}>Connect store</Button>
            </form>
          )}
          {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
          {shops.data.items.length > 0 && (
            <ul className="mt-4 divide-y divide-line text-sm">
              {shops.data.items.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-2 py-2">
                  <span className="font-medium text-ink">{s.shop_domain}</span>
                  <Badge tone={s.connected ? "ok" : "warn"}>{s.connected ? "connected" : "disconnected"}</Badge>
                  <span className="text-xs text-faint">{s.connected ? `since ${fmtDateTime(s.connected_at)}` : `since ${fmtDateTime(s.uninstalled_at)}`}</span>
                  {s.connected && (
                    <span className="ml-auto">
                      <ConfirmButton
                        title={`Disconnect ${s.shop_domain}?`}
                        body="Aurora deletes its access token and ignores further orders from this store. Sales already imported are kept. Also uninstall the app in Shopify to stop deliveries."
                        confirmLabel="Disconnect"
                        onConfirm={async () => {
                          await api(`/shopify/shops/${s.id}`, { method: "DELETE" });
                          await shops.mutate();
                        }}
                      >
                        Disconnect
                      </ConfirmButton>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
