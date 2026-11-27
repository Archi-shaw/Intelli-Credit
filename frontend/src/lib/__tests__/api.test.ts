import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, errorMessage } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

describe("api client", () => {
  it("sends the CSRF client header and JSON body on mutations", async () => {
    const f = mockFetch(201, { id: 1 });
    await api.post("/borrowers", { name: "X" });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/v1/borrowers");
    expect((init!.headers as Record<string, string>)["x-ic-client"]).toBe("web");
    expect(init!.credentials).toBe("same-origin");
    expect(init!.body).toBe(JSON.stringify({ name: "X" }));
  });
  it("turns error envelopes into ApiError with code and message", async () => {
    mockFetch(409, { error: { code: "duplicate_pan", message: "A borrower with this PAN already exists.", details: {} } });
    await expect(api.post("/borrowers", {})).rejects.toMatchObject({ status: 409, code: "duplicate_pan" });
  });
  it("broadcasts unauthorized responses so the shell can redirect to login", async () => {
    mockFetch(401, { error: { code: "session_expired", message: "Session expired." } });
    const seen = vi.fn();
    window.addEventListener("ic:unauthorized", seen);
    await expect(api.get("/dashboard")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener("ic:unauthorized", seen);
  });
  it("reports network failures with a readable message", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.get("/dashboard").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("network_error");
    expect(errorMessage(err)).toMatch(/Unable to reach the server/);
  });
});
