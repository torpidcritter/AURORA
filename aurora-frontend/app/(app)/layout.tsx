"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";

import { AppShell } from "@/components/AppShell";
import { ApiError, fetcher } from "@/lib/api";
import { SessionProvider } from "@/lib/session";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: false,
        // Do not hammer the API on client errors; retry transient failures a few times.
        onErrorRetry: (err, _key, _cfg, revalidate, { retryCount }) => {
          if (err instanceof ApiError && err.status < 500) return;
          if (retryCount >= 3) return;
          setTimeout(() => revalidate({ retryCount }), 1000 * 2 ** retryCount);
        },
      }}
    >
      <SessionProvider>
        <AppShell>{children}</AppShell>
      </SessionProvider>
    </SWRConfig>
  );
}
