import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("distinguishes a checksum sidecar from a completed verification", async () => {
    const onOperation = vi.fn(async () => true);
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
          last_result: "success",
          last_run_at: null,
          last_finished_at: null,
          last_exit_code: 0,
          next_run_at: null,
        }) as never;
      }
      return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
    });
    const user = userEvent.setup();

    render(
      <BackupsView refreshKey={0} operationBusy={false} onOperation={onOperation} />,
    );

    expect(await screen.findByText("校验文件就绪")).toBeTruthy();
    expect(screen.getByText(/不等于已经读完备份/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "校验并恢复" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "完整校验" }));
    expect(onOperation).toHaveBeenCalledWith("verify_backup", {
      backup_id: "backup_fixture",
    });
  });

  it("blocks restore when the checksum file is invalid", async () => {
    vi.spyOn(api, "get").mockImplementation((path) => {
      if (path === "/backups") {
        return Promise.resolve([
          {
            id: "backup_invalid",
            created_at: "2026-07-17T03:31:00+08:00",
            size_bytes: 2_200_000_000,
            verified: false,
            verification_status: "invalid",
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
          last_result: "success",
          last_run_at: null,
          last_finished_at: null,
          last_exit_code: 0,
          next_run_at: null,
        }) as never;
      }
      return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
    });

    render(
      <BackupsView refreshKey={0} operationBusy={false} onOperation={vi.fn(async () => true)} />,
    );

    expect(await screen.findByText("校验文件无效")).toBeTruthy();
    expect(screen.getByRole("button", { name: "完整校验" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "校验并恢复" }).hasAttribute("disabled")).toBe(true);
  });
});
