// The auditor token needs the server's admin key; an expired token is replaced once.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetTokens, api, setAdminKeyRequester } from "../src/api/client";

function json(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), init);
}

beforeEach(() => _resetTokens());
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setAdminKeyRequester(null); });

function serverWithKey(key: string, tokenRequests: (string | null)[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/auth/token") {
      const sent = (init!.headers as Record<string, string>)["X-Admin-Key"] ?? null;
      tokenRequests.push(sent);
      return sent === key ? json({ access_token: "aud" }) : json({ detail: "needs key" }, { status: 403 });
    }
    return json([]);
  }));
  return tokenRequests;
}

describe("the admin key", () => {
  it("is asked for once when the server refuses an auditor token without it, then kept for the tab", async () => {
    const tokenRequests = serverWithKey("secret");
    const ask = vi.fn(async () => "secret");
    setAdminKeyRequester(ask);

    await api.flags("r1");
    expect(ask).toHaveBeenCalledTimes(1);
    expect(tokenRequests).toEqual([null, "secret"]);

    // A token that merely expired is replaced with the remembered key, without asking again.
    tokenRequests.length = 0;
    ask.mockClear();
    _resetTokens();
    sessionStorage.setItem("mycroft-admin-key", "secret");
    await api.flags("r1");
    expect(ask).not.toHaveBeenCalled();
    expect(tokenRequests).toEqual(["secret"]);
  });

  it("is asked for once however many requests are waiting on it", async () => {
    serverWithKey("secret");
    let release!: (key: string | null) => void;
    const ask = vi.fn(() => new Promise<string | null>((resolve) => { release = resolve; }));
    setAdminKeyRequester(ask);

    const all = Promise.all([api.flags("a"), api.flags("b"), api.flags("c")]);
    await vi.waitFor(() => expect(ask).toHaveBeenCalled());
    release("secret");
    await all;
    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("is never sent for an investor token, and an investor is never asked for it", async () => {
    const seen: (string | undefined)[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/token") {
        seen.push((init!.headers as Record<string, string>)["X-Admin-Key"]);
        return json({ access_token: "inv" });
      }
      return json([]);
    }));
    const ask = vi.fn(async () => "secret");
    setAdminKeyRequester(ask);
    sessionStorage.setItem("mycroft-admin-key", "secret");
    await api.runs("investor");
    expect(seen).toEqual([undefined]);
    expect(ask).not.toHaveBeenCalled();
  });

  it("says plainly that a key is needed when the question is declined", async () => {
    serverWithKey("secret");
    setAdminKeyRequester(async () => null);
    await expect(api.flags("r1")).rejects.toThrow(/needs the server's admin key, and none was given/);
  });

  it("says plainly that the key was not accepted when it is wrong, and does not keep it", async () => {
    serverWithKey("secret");
    setAdminKeyRequester(async () => "wrong");
    await expect(api.flags("r1")).rejects.toThrow(/was not accepted/);
    expect(sessionStorage.getItem("mycroft-admin-key")).toBeNull();
  });

  it("does not use window.prompt, which embedded browsers refuse", async () => {
    serverWithKey("secret");
    const prompt = vi.spyOn(window, "prompt");
    await expect(api.flags("r1")).rejects.toThrow(/none was given/);
    expect(prompt).not.toHaveBeenCalled();
  });
});

describe("a token the server no longer accepts", () => {
  it("is dropped and fetched once more", async () => {
    let tokens = 0;
    const authorizations: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/token") { tokens += 1; return json({ access_token: `t${tokens}` }); }
      const auth = (init!.headers as Record<string, string>).Authorization;
      authorizations.push(auth);
      return auth === "Bearer t1" ? json({ detail: "expired" }, { status: 401 }) : json([]);
    }));
    await expect(api.runs("investor")).resolves.toEqual([]);
    expect(authorizations).toEqual(["Bearer t1", "Bearer t2"]);
  });
});
