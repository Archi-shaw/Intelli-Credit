"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect } from "react";

import { api, ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

export function useMe() {
  return useQuery<Me, ApiError>({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000, retry: false });
}

export function useCan(permission: string): boolean {
  const { data } = useMe();
  return !!data?.permissions.includes(permission);
}

/** Redirects to /login whenever any API call reports an expired or missing session. */
export function useSessionGuard() {
  const router = useRouter();
  const qc = useQueryClient();
  useEffect(() => {
    const onUnauthorized = () => {
      qc.clear();
      const next = typeof window !== "undefined" ? window.location.pathname + window.location.search : "/";
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    };
    window.addEventListener("ic:unauthorized", onUnauthorized);
    return () => window.removeEventListener("ic:unauthorized", onUnauthorized);
  }, [router, qc]);
}

export function useLogout() {
  const router = useRouter();
  const qc = useQueryClient();
  return useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* session may already be gone */
    }
    qc.clear();
    router.replace("/login");
  }, [router, qc]);
}
