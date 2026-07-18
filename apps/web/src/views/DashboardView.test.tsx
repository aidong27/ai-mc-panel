import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DashboardData } from "../types";
import { DashboardView, readableAiAnswer, serverIdentityLabel } from "./DashboardView";

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
    summary: {
      cpu: "CPU 使用正常",
      memory: "内存充足",
      disk: "磁盘空间充足",
      performance: "暂无 TPS 数据",
    },
  },
  players: { online: 0, maximum: 10, players: [], stale: true },
  ai: {
    enabled: false,
    configured: false,
    model: null,
    requests_per_minute: 3,
    requests_per_day: 40,
    tokens_per_day: 30_000,
  },
  diagnostics: {
    level: "good",
    headline: "当前没有需要立即处理的事项",
    updated_at: "2026-07-16T08:00:00+08:00",
    checks: [
      { id: "server", tone: "good", title: "运行正常", detail: "服务器正在运行", target: "server" },
    ],
    signals: [],
    backup: {
      latest: null,
      age_hours: null,
      schedule: { hour: 3, minute: 30, keep: 7 },
      count: 0,
    },
    local_time: "2026-07-16T08:00:00+08:00",
  },
  quick_actions: [],
};

describe("DashboardView", () => {
  it("keeps AI answers as safe readable text without common markdown markers", () => {
    expect(readableAiAnswer("# 结论\n过去一周**没有玩家**，工具为 `get_player_activity`。"))
      .toBe("结论\n过去一周没有玩家，工具为 get_player_activity。");
  });

  it("does not render unknown runtime identity as if it were a version", () => {
    expect(serverIdentityLabel({
      ...dashboard.server,
      minecraft_version: "unknown",
      loader: "Unknown",
      loader_version: "unknown",
    })).toBe("Minecraft 版本待识别");
  });

  it("does not present unknown player data as an empty server", () => {
    render(
      <DashboardView
        data={dashboard}
        operationBusy={false}
        onNavigate={vi.fn()}
        onOperation={vi.fn(async () => true)}
        onMessage={vi.fn()}
      />,
    );

    expect(screen.getByText("在线状态待确认")).toBeTruthy();
    expect(screen.getByText("暂时无法从日志确认在线玩家，面板不会猜测人数。")).toBeTruthy();
    expect(screen.queryByText("现在没人在线，正适合做维护或备份。")).toBeNull();
  });
});
