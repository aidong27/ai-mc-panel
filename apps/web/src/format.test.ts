import { describe, expect, it } from "vitest";
import { formatTimestamp } from "./format";

describe("formatTimestamp", () => {
  it("formats Unix seconds as a readable date", () => {
    expect(formatTimestamp(1781525731.6808462)).toContain("2026");
  });

  it("keeps ISO timestamps readable", () => {
    expect(formatTimestamp("2026-07-15T01:00:00Z")).toContain("2026");
  });

  it("uses a friendly label for invalid timestamps", () => {
    expect(formatTimestamp("not-a-date")).toBe("时间未知");
  });
});
