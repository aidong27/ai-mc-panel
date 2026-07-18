import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OperationPreview } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

const restorePreview: OperationPreview = {
  action: "restore_backup",
  params: { backup_id: "backup_0123456789ab" },
  title: "恢复备份",
  reason: "将世界回退到选定时间点。",
  impact: "需要停服，在线玩家会断开。",
  rollback: "恢复前会创建当前状态的恢复点。",
  risk: "high",
  requires_confirmation: true,
  requires_second_confirmation: true,
  stops_server: true,
  creates_recovery_point: true,
  review: {
    operation_id: "confirm_fixture",
    human_title: "恢复备份",
    risk: "high",
    review_items: [
      { label: "备份 ID", value: "backup_0123456789ab" },
      { label: "备份时间", value: "2026-07-17 03:30" },
      { label: "备份大小", value: "2.1 GB" },
      { label: "完整性", value: "已完整校验" },
    ],
    impact: "需要停服，在线玩家会断开。",
    stops_server: true,
    recovery_plan: "恢复前会创建当前状态的恢复点。",
    assurance_expected: "完成归档身份和服务就绪检查。",
    params_hash: "a".repeat(64),
    expires_at: "2026-07-17T13:00:00Z",
  },
};

describe("ConfirmDialog", () => {
  it("requires the exact server name for the second high-risk confirmation", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn(async () => undefined);
    render(
      <ConfirmDialog
        operation={restorePreview}
        secondStep
        serverName="星光好友服"
        busy={false}
        onCancel={() => undefined}
        onConfirm={onConfirm}
      />,
    );

    const confirm = screen.getByRole("button", { name: "确认恢复" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    await user.type(screen.getByRole("textbox"), "星光好友服");
    expect(confirm.disabled).toBe(false);
    await user.click(confirm);

    expect(onConfirm).toHaveBeenCalledWith("星光好友服");
  });

  it("can be cancelled with Escape when no operation is running", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        operation={restorePreview}
        secondStep={false}
        serverName="星光好友服"
        busy={false}
        onCancel={onCancel}
        onConfirm={async () => undefined}
      />,
    );

    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows the exact frozen targets supplied by the backend", () => {
    render(
      <ConfirmDialog
        operation={restorePreview}
        secondStep={false}
        serverName="星光好友服"
        busy={false}
        onCancel={() => undefined}
        onConfirm={async () => undefined}
      />,
    );

    expect(screen.getByText("backup_0123456789ab")).toBeTruthy();
    expect(screen.getByText("2026-07-17 03:30")).toBeTruthy();
    expect(screen.getByText("2.1 GB")).toBeTruthy();
    expect(screen.getByText("已完整校验")).toBeTruthy();
  });
});
