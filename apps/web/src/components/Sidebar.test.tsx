import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("keeps professional tools collapsed until the user asks for them", async () => {
    const user = userEvent.setup();
    render(
      <Sidebar
        current="home"
        theme="light"
        onNavigate={vi.fn()}
        onToggleTheme={vi.fn()}
        onLogout={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "设置" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "高级工具" }));
    expect(screen.getByRole("button", { name: "设置" })).toBeTruthy();
  });
});
