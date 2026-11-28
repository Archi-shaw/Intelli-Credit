import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useJobRunner } from "@/lib/jobs";

afterEach(() => vi.restoreAllMocks());

function respond(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useJobRunner", () => {
  it("asks the API to run the next job on each status poll while work is pending", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond({ mode: "request", pending: 1, ran: null }));
    const { rerender } = renderHook(({ tick }) => useJobRunner(7, true, tick, [["docs", 7]]), { wrapper: wrapper(), initialProps: { tick: 1 } });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    expect(f.mock.calls[0][0]).toBe("/api/v1/assessments/7/jobs/run-next");
    expect((f.mock.calls[0][1]!.headers as Record<string, string>)["x-ic-client"]).toBe("web");
    rerender({ tick: 2 });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
  });

  it("keeps a single call in flight", async () => {
    let release: (r: Response) => void = () => {};
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>((res) => { release = res; }));
    const { rerender } = renderHook(({ tick }) => useJobRunner(7, true, tick, []), { wrapper: wrapper(), initialProps: { tick: 1 } });
    rerender({ tick: 2 });
    rerender({ tick: 3 });
    expect(f).toHaveBeenCalledTimes(1);
    release(respond({ mode: "request", pending: 0, ran: null }));
  });

  it("does nothing when idle and stops calling once the server reports a worker backend", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond({ mode: "celery", pending: 2, ran: null }));
    const { rerender } = renderHook(({ active, tick }) => useJobRunner(7, active, tick, []),
      { wrapper: wrapper(), initialProps: { active: false, tick: 1 } });
    expect(f).not.toHaveBeenCalled();
    rerender({ active: true, tick: 2 });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    rerender({ active: true, tick: 3 });
    rerender({ active: true, tick: 4 });
    await new Promise((r) => setTimeout(r, 20));
    expect(f).toHaveBeenCalledTimes(1);
  });
});
