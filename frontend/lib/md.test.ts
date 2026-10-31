import { describe, expect, it } from "vitest";
import { fmtDur, fmtSpoken, fmtTs, shiftDate, withGaps } from "./format";
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
    expect([22.9, 720, 15600].map(fmtSpoken)).toEqual(["22s", "12m", "4h 20m"]);
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

describe("timeline", () => {
  it("marks gaps without inventing events", () => {
    const ev = (s: string, e: string) => ({ start: `2026-10-31T${s}:00+05:30`, end: `2026-10-31T${e}:00+05:30` });
    const out = withGaps([ev("08:30", "09:00"), ev("09:05", "09:20"), ev("11:00", "11:30")]);
    expect(out.map((x) => ("gapMin" in x ? x.gapMin : "e"))).toEqual(["e", "e", 100, "e"]);
  });
  it("shifts dates across month ends", () => {
    expect(shiftDate("2026-10-31", 1)).toBe("2026-11-01");
    expect(shiftDate("2026-03-01", -1)).toBe("2026-02-28");
  });
});
