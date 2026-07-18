import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, AUTH_EXPIRED_EVENT } from "./api";
import { App } from "./App";
import type { DashboardData } from "./types";

const dashboard: DashboardData = {
  server: {
    state: "running",
    healthy: true,
    friendly_status: "一切正常",
    service: "minecraft.service",
    minecraft_version: "1.20.1",
    loader: "Forge",
    loader_version: "47.4.0",
    java_version: "17",
    uptime_seconds: 100,
    fingerprint: "fixture",
    adapter: "systemd",
  },
  server_name: "星光好友服",
  connection_address: null,
  metrics: {
    cpu_percent: 10,
    memory_used_bytes: 4,
    memory_total_bytes: 16,
    disk_used_bytes: 20,
    disk_total_bytes: 100,
    tps: null,
    summary: { cpu: "CPU 使用正常", memory: "内存充足", disk: "磁盘空间充足", performance: "暂无 TPS 数据" },
  },
  players: { online: 0, maximum: 10, players: [], stale: false },
  ai: { enabled: false, configured: false, model: null, requests_per_minute: 3, requests_per_day: 40, tokens_per_day: 30_000 },
  capabilities: { console_commands_enabled: false },
  diagnostics: {
    level: "good",
    headline: "当前没有需要立即处理的事项",
    updated_at: "2026-07-16T08:00:00+08:00",
    checks: [],
    signals: [],
    backup: { latest: null, age_hours: null, schedule: { hour: 3, minute: 30, keep: 7 }, count: 0 },
    local_time: "2026-07-16T08:00:00+08:00",
  },
  quick_actions: [],
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("App authentication lifecycle", () => {
  it("removes stale dashboard data and returns to login when the session expires", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    vi.spyOn(api, "me").mockResolvedValue({ username: "admin", role: "owner", csrf_token: "csrf" });
    vi.spyOn(api, "get").mockResolvedValue(dashboard as never);

    const { container } = render(<App />);
    expect(await screen.findByText("服务器首页")).toBeTruthy();

    const currentNavigation = container.querySelector<HTMLButtonElement>(".desktop-sidebar .nav-item.active");
    expect(currentNavigation).not.toBeNull();
    fireEvent.click(currentNavigation as HTMLButtonElement);
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "auto" });

    act(() => window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)));

    expect(await screen.findByRole("button", { name: "登录" })).toBeTruthy();
    expect(screen.getByText("登录已过期，请重新登录。")).toBeTruthy();
    expect(screen.queryByText("服务器首页")).toBeNull();
  });

  it("does not misrepresent a dashboard outage as a login failure", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    vi.spyOn(api, "me").mockResolvedValue({ username: "admin", role: "owner", csrf_token: "csrf" });
    vi.spyOn(api, "get").mockRejectedValue(new Error("面板后端暂时不可用"));

    render(<App />);

    expect(await screen.findByText("服务器状态暂时读不到")).toBeTruthy();
    expect(screen.getByText("面板后端暂时不可用")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
  });
});
