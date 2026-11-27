import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }), useSearchParams: () => new URLSearchParams("next=/borrowers") }));

import LoginPage from "@/app/(auth)/login/page";

const wrap = () => render(<QueryClientProvider client={new QueryClient()}><LoginPage /></QueryClientProvider>);

beforeEach(() => replace.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("login page", () => {
  it("validates required fields before calling the API", async () => {
    const f = vi.spyOn(globalThis, "fetch");
    wrap();
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("E-mail is required")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
  });
  it("toggles password visibility", async () => {
    wrap();
    const pw = await screen.findByLabelText("Password");
    expect(pw).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(pw).toHaveAttribute("type", "text");
  });
  it("shows the server's error message on bad credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "invalid_credentials", message: "Invalid e-mail or password." } }), { status: 401 }));
    wrap();
    await userEvent.type(await screen.findByLabelText("Work e-mail"), "a@b.example");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Invalid e-mail or password.")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
  it("redirects to the requested page after a successful sign-in", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ user: {}, role: "analyst" }), { status: 200 }));
    wrap();
    await userEvent.type(await screen.findByLabelText("Work e-mail"), "a@b.example");
    await userEvent.type(screen.getByLabelText("Password"), "Correct-pass1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/borrowers"));
  });
});
