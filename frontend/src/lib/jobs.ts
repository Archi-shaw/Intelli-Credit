"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

export interface RunNextResult { mode: string; pending: number; ran: Job | null }

/**
 * Drives request-triggered processing when the API runs without a background worker (JOB_BACKEND=request,
 * e.g. on Vercel). While `active` (the page shows queued or processing work), each status poll (`tick`) asks the
 * API to run the application's next queued job inside that request; at most one call is in flight per hook.
 * The API claims jobs atomically, so several tabs or users calling at once never run a job twice, and a job left
 * behind by a closed tab simply runs on the next visit. With a worker backend (celery/thread) the first response
 * says so and the hook stops calling.
 */
export function useJobRunner(appId: number, active: boolean, tick: number, invalidate: readonly unknown[][]) {
  const qc = useQueryClient();
  const inFlight = useRef(false);
  const workerMode = useRef(false);
  const keys = useRef(invalidate);
  keys.current = invalidate;

  useEffect(() => {
    if (!active || inFlight.current || workerMode.current) return;
    inFlight.current = true;
    api.post<RunNextResult>(`/assessments/${appId}/jobs/run-next`)
      .then((r) => {
        if (r.mode !== "request") workerMode.current = true;
        keys.current.forEach((queryKey) => qc.invalidateQueries({ queryKey }));
      })
      .catch(() => { /* transient (rate limit, timeout): the next status poll tries again */ })
      .finally(() => { inFlight.current = false; });
  }, [appId, active, tick, qc]);
}
