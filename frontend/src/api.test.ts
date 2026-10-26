import { ApiError, get, post, qs, setCsrf } from "./api";

function mockFetch(status: number, body: unknown, ctype = "application/json") {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status, statusText: "x",
    headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? ctype : null) },
    json: async () => body, text: async () => String(body),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => { vi.unstubAllGlobals(); setCsrf(null); });

describe("api client", () => {
  it("sends the CSRF token on state-changing requests only, and never in the URL", async () => {
    const fn = mockFetch(200, { ok: true });
    setCsrf("tok-123");
    await post("/api/x", { a: 1 });
    await get("/api/y");
    const [postCall, getCall] = fn.mock.calls;
    expect(postCall[1].headers["X-CSRF-Token"]).toBe("tok-123");
    expect(getCall[1].headers["X-CSRF-Token"]).toBeUndefined();
    expect(postCall[0]).not.toContain("tok-123");
    expect(postCall[1].credentials).toBe("same-origin");
  });

  it("turns validation errors into readable messages with field names", async () => {
    mockFetch(422, { detail: [{ field: "daily_cap", message: "too high" }, { field: "", message: "other" }] });
    await expect(post("/api/settings/sending", {})).rejects.toMatchObject({ status: 422, message: "daily_cap: too high; other", fields: [{ field: "daily_cap", message: "too high" }, { field: "", message: "other" }] });
  });

  it("announces an expired session (401) but not for the sign-in endpoints", async () => {
    const seen = vi.fn();
    window.addEventListener("auth:expired", seen);
    mockFetch(401, { detail: "Not signed in" });
    await expect(get("/api/overview")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledTimes(1);
    await expect(post("/api/auth/login", {})).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener("auth:expired", seen);
  });

  it("reports an unreachable server in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fail")));
    await expect(get("/api/x")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Cannot reach the server") });
  });

  it("builds query strings, skipping empty values", () => {
    expect(qs({ a: "x y", b: "", c: undefined, d: false, e: 0, f: "true" })).toBe("?a=x+y&e=0&f=true");
    expect(qs({})).toBe("");
  });
});
