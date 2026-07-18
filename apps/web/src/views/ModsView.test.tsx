import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ModInfo } from "../types";
import { ModsView } from "./ModsView";

afterEach(() => vi.restoreAllMocks());

describe("ModsView", () => {
  it("searches the full mod collection even when only the first page is visible", async () => {
    const user = userEvent.setup();
    const mods: ModInfo[] = Array.from({ length: 45 }, (_, index) => ({
      id: `mod_${index}`,
      name: `Test Mod ${index}`,
      filename: `test-mod-${index}.jar`,
      version: "1.0.0",
      loader: "Forge-compatible",
      enabled: true,
      duplicate: false,
    }));
    vi.spyOn(api, "get").mockResolvedValue(mods as never);
    render(<ModsView refreshKey={0} operationBusy={false} onOperation={async () => true} onMessage={vi.fn()} />);

    await screen.findByText("Test Mod 0");
    expect(screen.queryByText("Test Mod 44")).toBeNull();
    await user.type(screen.getByPlaceholderText("搜索模组名、文件名或版本"), "Test Mod 44");
    expect(await screen.findByText("Test Mod 44")).toBeTruthy();
  });
});
