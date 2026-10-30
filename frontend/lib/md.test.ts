import { describe, expect, it } from "vitest";
import { fmtDur, fmtTs } from "./format";
import { parseBlocks } from "./md";

describe("format", () => {
  it("formats timestamps", () => {
    expect(fmtTs(65000)).toBe("1:05");
    expect(fmtTs(3_725_000)).toBe("1:02:05");
  });
  it("formats durations", () => {
    expect(fmtDur(null)).toBe("—");
    expect(fmtDur(90)).toBe("2m");
    expect(fmtDur(3900)).toBe("1h 5m");
  });
});

describe("answer markdown", () => {
  it("parses bullets, tables and paragraphs without losing citation markers", () => {
    const b = parseBlocks("**Decisions**\n- Go with tiers [S2]\n\n| a | b |\n|---|---|\n| 1 | 2 |");
    expect(b.map((x) => x.type)).toEqual(["p", "ul", "table"]);
    expect((b[1] as any).items[0]).toContain("[S2]");
    expect((b[2] as any).rows).toEqual([["1", "2"]]);
  });
});
