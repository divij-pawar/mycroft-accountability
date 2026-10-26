// The admin key dialog: the app asks in the page when the server wants its admin key.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "../src/App";
import { _resetTokens } from "../src/api/client";
import { AdminKeyDialog } from "../src/components/AdminKeyDialog";

const json = (body: unknown, init?: ResponseInit) => new Response(JSON.stringify(body), init);

function server(key: string) {
  const tokenKeys: (string | null)[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/auth/token") {
      const sent = (init!.headers as Record<string, string>)["X-Admin-Key"] ?? null;
      tokenKeys.push(sent);
      const scope = JSON.parse(init!.body as string).scope;
      return scope === "investor" || sent === key ? json({ access_token: `t-${scope}` }) : json({ detail: "needs key" }, { status: 403 });
    }
    if (url === "/api/directive") return json({ version: "v1.5.2", text: "" });
    if (url === "/api/self-report") return json({ counts: { issues_open: 0 } });
    return json([]);
  }));
  return tokenKeys;
}

beforeEach(() => { _resetTokens(); window.location.hash = "#/analysis"; });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("AdminKeyDialog", () => {
  it("hides the key as it is typed, and only continues with one", () => {
    const onSubmit = vi.fn();
    render(<AdminKeyDialog onSubmit={onSubmit} onCancel={() => {}} />);
    const input = screen.getByLabelText("Admin key");
    expect(input).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "  k3y " } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onSubmit).toHaveBeenCalledWith("k3y");
  });

  it("cancels on Escape and on the button", () => {
    const onCancel = vi.fn();
    render(<AdminKeyDialog onSubmit={() => {}} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });
});

describe("App, auditor scope, a server that wants its key", () => {
  it("opens one dialog, and the waiting requests carry on once the key is given", async () => {
    const tokenKeys = server("the-key");
    render(<App />);
    const dialog = await screen.findByRole("dialog", { name: "Auditor access needs the admin key" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Admin key"), { target: { value: "the-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(tokenKeys).toContain("the-key");
    expect(sessionStorage.getItem("mycroft-admin-key")).toBe("the-key");
    expect(screen.queryByText(/none was given/)).toBeNull();
  });

  it("cancelling leaves a plain message, and Investor still works without a key", async () => {
    server("the-key");
    render(<App />);
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Investor" }));
    await screen.findByText("Earlier signals");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
