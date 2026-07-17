import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ServerToolsView } from "./ServerToolsView";

class FakeWebSocket {
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  close() {}
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ServerToolsView", () => {
  it("hides known repetitive INFO noise by default and opens crash details in-page", async () => {
    const user = userEvent.setup();
    const noisy = "[12:00:00] [Server thread/INFO] [AllTheLeaks/]: repetitive scan";
    const useful = "[12:00:01] [Server thread/INFO] [minecraft/]: player joined";
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    vi.spyOn(api, "mutate").mockResolvedValue({ ticket: "ticket" } as never);
    vi.spyOn(api, "get").mockImplementation((path) => {
      if (path.startsWith("/logs/recent")) return Promise.resolve({ lines: [noisy, useful] }) as never;
      if (path === "/crash-reports") return Promise.resolve([{ id: "crash_123456", filename: "crash-test.txt", created_at: 1_700_000_000, size_bytes: 2048 }]) as never;
      if (path === "/crash-reports/crash_123456") return Promise.resolve({ excerpt: "Example stack trace" }) as never;
      return Promise.reject(new Error(`unexpected path: ${path}`)) as never;
    });

    render(<ServerToolsView refreshKey={0} operationBusy={false} onOperation={async () => true} onMessage={vi.fn()} />);
    const output = await screen.findByLabelText("Minecraft 日志");
    await waitFor(() => expect(output.textContent).toContain(useful));
    expect(output.textContent).not.toContain(noisy);

    await user.click(screen.getByRole("button", { name: "全部" }));
    await waitFor(() => expect(output.textContent).toContain(noisy));

    await user.click(await screen.findByRole("button", { name: "查看" }));
    expect(await screen.findByText("Example stack trace")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "crash-test.txt" })).toBeTruthy();
  });
});
