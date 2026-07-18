import { expect, test, type Page } from "@playwright/test";

const username = "admin";
const password = ["browser", "fixture", "only", "2026"].join("-");

async function login(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await page.getByLabel("管理员账号").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "服务器首页" })).toBeVisible();
}

test("login survives refresh and confirmation flows do not execute on cancel", async ({ page }) => {
  await login(page);
  await expect(page.getByText("服务器正在稳定运行")).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "服务器首页" })).toBeVisible();

  await page.getByRole("button", { name: /停止服务器/ }).click();
  const stopDialog = page.getByRole("dialog", { name: "停止服务器" });
  await expect(stopDialog).toBeVisible();
  await expect(stopDialog.getByText(/在线玩家会断开/)).toBeVisible();
  await expect(stopDialog.getByText("目标服务")).toBeVisible();
  await expect(stopDialog.getByText("minecraft.service")).toBeVisible();
  await stopDialog.getByRole("button", { name: "取消" }).click();
  await expect(stopDialog).toBeHidden();
  await expect(page.getByText("服务器正在稳定运行")).toBeVisible();

  await page.locator(".desktop-sidebar").getByRole("button", { name: "备份", exact: true }).click();
  await expect(page.getByRole("heading", { name: "备份与恢复" })).toBeVisible();
  await expect(page.getByText("校验文件就绪").first()).toBeVisible();

  await page.getByRole("button", { name: "校验并恢复" }).first().click();
  const restoreDialog = page.getByRole("dialog", { name: "恢复旧备份" });
  await expect(restoreDialog.getByText("高风险操作")).toBeVisible();
  await expect(restoreDialog.getByText("备份 ID")).toBeVisible();
  await expect(restoreDialog.getByText("备份时间")).toBeVisible();
  await expect(restoreDialog.getByText("备份大小")).toBeVisible();
  await expect(restoreDialog.getByText("完整性")).toBeVisible();
  await restoreDialog.getByRole("button", { name: "确认执行" }).click();
  await expect(restoreDialog.getByText(/请输入服务器名称/)).toBeVisible();
  await expect(restoreDialog.getByRole("button", { name: "确认恢复" })).toBeDisabled();
  await restoreDialog.getByRole("button", { name: "取消" }).click();
  await expect(restoreDialog).toBeHidden();

  await page.getByRole("button", { name: "完整校验" }).first().click();
  await expect(page.getByText("已完整校验").first()).toBeVisible();
  await expect(page.getByRole("status")).toContainText("操作已完成");
});

test("AI proposals and manual console use the same exact backend review", async ({ page }) => {
  await page.route("**/api/v1/ai/chat", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        data: {
          answer: "我可以提出授予 Alex 管理员权限，但需要你确认。",
          evidence: [],
          proposed_actions: [
            {
              action: "add_operator",
              params: { player: "Alex" },
              title: "授予管理员权限",
              reason: "允许指定玩家使用高权限游戏命令。",
              impact: "离线模式下存在用户名冒用风险。",
              risk: "medium",
              requires_confirmation: true,
              requires_second_confirmation: false,
              stops_server: false,
              creates_recovery_point: false,
              rollback: "无需数据回滚",
            },
          ],
          limits: {},
        },
        error: null,
        request_id: "req_e2e_ai_proposal",
      }),
    });
  });
  await login(page);

  await page.getByPlaceholder(/为什么服务器这么卡/).fill("让 Alex 成为管理员");
  await page.getByRole("button", { name: "交给 AI" }).click();
  await page.getByRole("button", { name: "查看并执行：授予管理员权限" }).click();
  let operatorDialog = page.getByRole("dialog", { name: "授予管理员权限" });
  await expect(operatorDialog.getByText("玩家", { exact: true })).toBeVisible();
  await expect(operatorDialog.getByText("Alex", { exact: true })).toBeVisible();
  await operatorDialog.getByRole("button", { name: "取消" }).click();

  await page.getByRole("button", { name: "查看并执行：授予管理员权限" }).click();
  operatorDialog = page.getByRole("dialog", { name: "授予管理员权限" });
  await operatorDialog.getByRole("button", { name: "确认执行" }).click();
  await expect(page.getByRole("status")).toContainText("操作已完成");
  const operators = await page.evaluate(async () => {
    const response = await fetch("/api/v1/operators");
    return (await response.json()).data as string[];
  });
  expect(operators).toContain("Alex");

  const sidebar = page.locator(".desktop-sidebar");
  await sidebar.getByRole("button", { name: "高级工具" }).click();
  await sidebar.getByRole("button", { name: "服务器工具" }).click();
  const command = "say [验收]  精确确认";
  await page.getByPlaceholder(/输入 Minecraft 控制台命令/).fill(command);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const consoleDialog = page.getByRole("dialog", { name: "发送控制台命令" });
  await expect(consoleDialog.getByText("命令", { exact: true })).toBeVisible();
  const commandValue = consoleDialog.getByText(command, { exact: true });
  await expect(commandValue).toBeVisible();
  expect(await commandValue.textContent()).toBe(command);
  await expect(consoleDialog.getByText(/不证明游戏内效果/)).toBeVisible();
  await consoleDialog.getByRole("button", { name: "取消" }).click();
});

test("mobile navigation, theme persistence, and logout remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    if (!window.localStorage.getItem("mc-panel-theme")) {
      window.localStorage.setItem("mc-panel-theme", "light");
    }
  });
  await login(page);

  await page.getByRole("button", { name: "打开导航" }).click();
  const drawer = page.locator(".mobile-sidebar.open .sidebar");
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: "深色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await drawer.getByRole("button", { name: "备份", exact: true }).click();
  await expect(page.getByRole("heading", { name: "备份与恢复" })).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(hasHorizontalOverflow).toBe(false);

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator(".mobile-sidebar.open .sidebar").getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
});
