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
  webhooks: Record<string, string>;
  webhooks_ok: boolean;
  webhooks_checked_at: string | null;
}

const SHOP_RE = /^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$/;

/** Admin-only card: connect a Shopify store (OAuth) and see connected stores. */
export function ShopifyCard() {
  const params = useSearchParams();
  const shops = useSWR<{ items: Shop[]; configured: boolean }>("/shopify/shops");
  const [shop, setShop] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState<string | null>(null);
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

  const refreshWebhooks = async (id: string) => {
    setErr(null);
    setRefreshing(id);
    try {
      await api(`/shopify/shops/${id}/webhooks`, { method: "POST" });
      await shops.mutate();
    } catch (e2) {
      setErr(e2);
    } finally {
      setRefreshing(null);
    }
  };

  return (
    <Card title="Shopify" subtitle="New, cancelled and refunded orders from connected stores update daily sales automatically (source: shopify).">
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
                  {s.connected && <Badge tone={s.webhooks_ok ? "ok" : "warn"}>{s.webhooks_ok ? "receiving orders" : "webhooks not registered"}</Badge>}
                  {s.connected && (
                    <span className="ml-auto flex gap-2">
                      <Button className="px-2.5 py-1 text-xs" loading={refreshing === s.id} onClick={() => refreshWebhooks(s.id)}>
                        Re-register webhooks
                      </Button>
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
                  {s.connected && !s.webhooks_ok && Object.keys(s.webhooks).length > 0 && (
                    <ul className="w-full text-xs text-muted" aria-label={`Webhook status for ${s.shop_domain}`}>
                      {Object.entries(s.webhooks).map(([topic, st]) => (
                        <li key={topic}>
                          <span className="font-mono">{topic}</span>: {st}
                        </li>
                      ))}
                    </ul>
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
