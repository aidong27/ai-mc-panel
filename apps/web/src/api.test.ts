import { afterEach, describe, expect, it, vi } from "vitest";
import { api, AUTH_EXPIRED_EVENT } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("ApiClient", () => {
  it("turns a non-JSON server failure into a friendly structured error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        json: async () => {
          throw new SyntaxError("not json");
        },
      } as unknown as Response)),
    );

    await expect(api.get("/health")).rejects.toMatchObject({
      code: "invalid_server_response",
      message: "面板服务暂时异常，请稍后重试",
    });
  });

  it("notifies the application when the authenticated session expires", async () => {
    const expired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        json: async () => ({
          ok: false,
          data: null,
          error: { code: "authentication_required", message: "请重新登录", details: {} },
          request_id: "req_test",
        }),
      } as unknown as Response)),
    );

    await expect(api.get("/dashboard")).rejects.toMatchObject({ code: "authentication_required" });
    expect(expired).toHaveBeenCalledOnce();
    window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  });
});
