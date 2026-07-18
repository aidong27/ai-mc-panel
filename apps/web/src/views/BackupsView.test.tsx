import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { BackupsView } from "./BackupsView";

afterEach(() => vi.restoreAllMocks());

describe("BackupsView", () => {
  it("shows an automatic backup failure as a visible warning", async () => {
    vi.spyOn(api, "get").mockImplementation((path) => {
      if (path === "/backups") {
        return Promise.resolve([
          {
            id: "backup_fixture",
            created_at: "2026-07-17T03:31:00+08:00",
            size_bytes: 2_200_000_000,
            verified: false,
            verification_status: "checksum_present",
            kind: "cold",
          },
        ]) as never;
      }
      if (path === "/backup-schedule") {
        return Promise.resolve({ hour: 3, minute: 30, keep: 7 }) as never;
      }
      if (path === "/backup-status") {
        return Promise.resolve({
          timer_active: true,
          timer_enabled: true,
          last_result: "failed",
          last_run_at: "2026-07-17T03:30:00+08:00",
          last_finished_at: "2026-07-17T03:31:00+08:00",
          last_exit_code: 1,
          next_run_at: "2026-07-18T03:30:00+08:00",
        }) as never;
      }
      return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
    });

    render(
      <BackupsView
        refreshKey={0}
        operationBusy={false}
        onOperation={vi.fn(async () => true)}
      />,
    );

    expect(await screen.findByText(/上次自动备份失败/)).toBeTruthy();
    expect(screen.getByText(/退出码 1/)).toBeTruthy();
    expect(screen.getByText("1 / 7 份")).toBeTruthy();
  });
});
