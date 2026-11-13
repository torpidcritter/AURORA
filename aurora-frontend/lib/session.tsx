"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import useSWR from "swr";

import { fetcher, setUnauthorizedHandler } from "@/lib/api";
import type { Me } from "@/lib/types";

interface SessionValue {
  me: Me | undefined;
  loading: boolean;
  can: (perm: string) => boolean;
}

const SessionContext = createContext<SessionValue>({ me: undefined, loading: true, can: () => false });

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { data, isLoading } = useSWR<Me>("/auth/me", fetcher, { revalidateOnFocus: true, shouldRetryOnError: false });
  useEffect(() => {
    setUnauthorizedHandler(() => {
      const next = window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    });
    return () => setUnauthorizedHandler(null);
  }, [router]);
  const can = (perm: string) => !!data?.permissions.includes(perm);
  return <SessionContext.Provider value={{ me: data, loading: isLoading, can }}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);
