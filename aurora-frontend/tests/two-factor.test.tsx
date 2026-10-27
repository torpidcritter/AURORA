import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/app/login/LoginForm";
import { RecoveryCodes } from "@/components/TwoFactor";
import { cleanCode, codeProblem, groupSecret, recoveryCodesText } from "@/lib/twoFactor";

const router = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams("next=/purchase-orders"),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  router.replace.mockReset();
});

describe("two-factor helpers", () => {
  it("cleans and checks codes", () => {
    expect(cleanCode(" 123 456 ")).toBe("123456");
    expect(codeProblem("123 456")).toBeNull();
    expect(codeProblem("12345")).toMatch(/6-digit/);
    expect(codeProblem("abcdef")).toMatch(/6-digit/);
    expect(groupSecret("JBSWY3DPEHPK3PXP")).toBe("JBSW Y3DP EHPK 3PXP");
    expect(recoveryCodesText(["abcde-fghjk"], "a@b.co")).toContain("abcde-fghjk");
  });

  it("shows recovery codes once with a download", () => {
    const done = vi.fn();
    render(<RecoveryCodes codes={["abcde-fghjk", "mnpqr-stuvw"]} email="a@b.co" onDone={done} />);
    expect(screen.getByRole("list", { name: "Recovery codes" }).children).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "I have saved them" }));
    expect(done).toHaveBeenCalled();
  });
});

describe("sign-in with a second factor", () => {
  it("asks for the code when the account needs one, then sends it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "two_factor_required", message: "Enter the code" } }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ana@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const codeField = await screen.findByLabelText("Authentication code");
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual({ email: "ana@example.com", password: "pw" });

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Enter the code from your authenticator app/)).toBeInTheDocument();
    fireEvent.change(codeField, { target: { value: " 123456 " } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/purchase-orders"));
    expect(JSON.parse((fetchMock.mock.calls[1]?.[1] as RequestInit).body as string)).toEqual({ email: "ana@example.com", password: "pw", otp: "123456" });
  });
});
