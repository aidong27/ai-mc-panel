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
  await stopDialog.getByRole("button", { name: "取消" }).click();
  await expect(stopDialog).toBeHidden();
  await expect(page.getByText("服务器正在稳定运行")).toBeVisible();

  await page.locator(".desktop-sidebar").getByRole("button", { name: "备份", exact: true }).click();
  await expect(page.getByRole("heading", { name: "备份与恢复" })).toBeVisible();
  await expect(page.getByText("校验文件就绪").first()).toBeVisible();

  await page.getByRole("button", { name: "校验并恢复" }).first().click();
  const restoreDialog = page.getByRole("dialog", { name: "恢复旧备份" });
  await expect(restoreDialog.getByText("高风险操作")).toBeVisible();
  await restoreDialog.getByRole("button", { name: "确认执行" }).click();
  await expect(restoreDialog.getByText(/请输入服务器名称/)).toBeVisible();
  await expect(restoreDialog.getByRole("button", { name: "确认恢复" })).toBeDisabled();
  await restoreDialog.getByRole("button", { name: "取消" }).click();
  await expect(restoreDialog).toBeHidden();

  await page.getByRole("button", { name: "完整校验" }).first().click();
  await expect(page.getByText("已完整校验").first()).toBeVisible();
  await expect(page.getByRole("status")).toContainText("操作已完成");
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
