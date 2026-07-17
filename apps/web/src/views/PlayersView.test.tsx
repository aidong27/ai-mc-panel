import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { PlayersView } from "./PlayersView";

afterEach(() => vi.restoreAllMocks());

describe("PlayersView", () => {
  it("keeps persisted history visible when the collector is unhealthy", async () => {
    vi.spyOn(api, "get").mockImplementation((path) => {
      if (path === "/whitelist" || path === "/operators") return Promise.resolve([]) as never;
      if (path === "/properties") return Promise.resolve({ "white-list": true }) as never;
      if (path === "/server/player-activity?days=7") {
        return Promise.resolve({
          days: 7,
          requested_start: "2026-07-11T00:00:00+08:00",
          generated_at: "2026-07-17T11:00:00+08:00",
          coverage_start: "2026-07-01T00:00:00+08:00",
          coverage_end: "2026-07-17T10:50:00+08:00",
          coverage_complete: true,
          truncated: false,
          sessions: 1,
          unique_players: 1,
          daily: [
            { date: "2026-07-11", sessions: 1, unique_players: 1, players: ["Alice"] },
            ...[12, 13, 14, 15, 16, 17].map((day) => ({
              date: `2026-07-${day}`,
              sessions: 0,
              unique_players: 0,
              players: [],
            })),
          ],
          recent_players: [
            { name: "Alice", sessions: 1, last_joined_at: "2026-07-11T20:00:00+08:00" },
          ],
          source: "persisted_minecraft_login_history",
          history: {
            persisted: true,
            last_imported_at: "2026-07-17T10:50:00+08:00",
            collector_healthy: false,
          },
        }) as never;
      }
      return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
    });

    render(
      <PlayersView
        players={{ online: 0, maximum: 10, players: [] }}
        refreshKey={0}
        operationBusy={false}
        onOperation={vi.fn(async () => true)}
      />,
    );

    expect(await screen.findByText("历史已保存")).toBeTruthy();
    expect(screen.getByText(/新记录可能延迟/)).toBeTruthy();
    expect(screen.getByText("1 位朋友，1 次进入")).toBeTruthy();
  });
});
