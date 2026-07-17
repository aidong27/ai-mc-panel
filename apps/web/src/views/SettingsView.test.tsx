import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { SettingsView } from "./SettingsView";

const aiSettings = {
  enabled: true,
  configured: true,
  api_base_url: "https://api.example.test",
  api_key_configured: true,
  model: "chat-model",
  temperature: 0.2,
  max_tokens: 800,
  timeout_seconds: 30,
  requests_per_minute: 3,
  requests_per_day: 40,
  tokens_per_day: 30_000,
};

function mockSettingsReads() {
  vi.spyOn(api, "get").mockImplementation((path) => {
    if (path === "/properties") return Promise.resolve({ pvp: true, "white-list": false, difficulty: "normal" }) as never;
    if (path === "/ai/settings") return Promise.resolve(aiSettings) as never;
    if (path === "/ai/usage") return Promise.resolve({ day: "2026-07-16", request_count: 2, input_tokens: 100, output_tokens: 50 }) as never;
    return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
  });
}

afterEach(() => vi.restoreAllMocks());

describe("SettingsView", () => {
  it("renders typed boolean properties as switches instead of text fields", async () => {
    mockSettingsReads();
    render(<SettingsView refreshKey={0} operationBusy={false} onOperation={async () => true} onMessage={vi.fn()} />);

    const pvpRow = (await screen.findByText("玩家互相伤害")).closest(".property-row");
    expect(pvpRow).not.toBeNull();
    expect((within(pvpRow as HTMLElement).getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    expect(within(pvpRow as HTMLElement).queryByRole("textbox")).toBeNull();
  });

  it("changes the password without putting it in an operation payload", async () => {
    const user = userEvent.setup();
    mockSettingsReads();
    const changePassword = vi.spyOn(api, "changePassword").mockResolvedValue({ changed: true, csrf_token: "rotated", sessions_revoked: true });
    render(<SettingsView refreshKey={0} operationBusy={false} onOperation={async () => true} onMessage={vi.fn()} />);

    await screen.findByText("更改登录密码");
    await user.type(screen.getByLabelText("当前密码"), "current-password");
    await user.type(screen.getByLabelText("新密码"), "new-password-123");
    await user.type(screen.getByLabelText("再输入一次"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "更新密码" }));

    await waitFor(() => expect(changePassword).toHaveBeenCalledWith("current-password", "new-password-123"));
  });
});
