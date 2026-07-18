import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LoginView } from "./LoginView";

describe("LoginView", () => {
  it("submits credentials without exposing the password by default", async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn(async () => undefined);
    render(<LoginView busy={false} error="" onLogin={onLogin} />);

    const password = screen.getByLabelText("密码") as HTMLInputElement;
    expect(password.type).toBe("password");

    await user.type(screen.getByLabelText("管理员账号"), "owner");
    await user.type(password, "test-secret");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(onLogin).toHaveBeenCalledWith("owner", "test-secret");
  });

  it("lets the user reveal and hide the password", async () => {
    const user = userEvent.setup();
    render(<LoginView busy={false} error="" onLogin={async () => undefined} />);

    const password = screen.getByLabelText("密码") as HTMLInputElement;
    await user.click(screen.getByRole("button", { name: "显示密码" }));
    expect(password.type).toBe("text");
    await user.click(screen.getByRole("button", { name: "隐藏密码" }));
    expect(password.type).toBe("password");
  });

  it("uses deployment-neutral security wording", () => {
    render(<LoginView busy={false} error="" onLogin={async () => undefined} />);

    expect(screen.getByText(/登录会话通过加密连接传输/)).toBeTruthy();
    expect(screen.queryByText(/SSH 隧道/)).toBeNull();
  });
});
