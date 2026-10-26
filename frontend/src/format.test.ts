import { ago, compact, fmtDuration, fmtNum, fmtUsd, label, pct } from "./format";

describe("format helpers", () => {
  it("formats numbers with separators and dashes for missing values", () => {
    expect(fmtNum(21860)).toBe("21,860");
    expect(fmtNum(null)).toBe("–");
    expect(fmtNum(undefined)).toBe("–");
  });
  it("shows tiny costs with enough precision to be meaningful", () => {
    expect(fmtUsd(0.0016)).toBe("$0.0016");
    expect(fmtUsd(12.3456)).toBe("$12.35");
    expect(fmtUsd(0)).toBe("$0.00");
  });
  it("turns snake_case statuses into labels", () => {
    expect(label("review_required")).toBe("Review required");
    expect(label(null)).toBe("–");
  });
  it("compacts axis numbers", () => {
    expect(compact(950)).toBe("950");
    expect(compact(1200)).toBe("1.2K");
    expect(compact(21000)).toBe("21K");
    expect(compact(2_000_000)).toBe("2M");
  });
  it("formats durations and relative times", () => {
    expect(fmtDuration(42)).toBe("42s");
    expect(fmtDuration(125)).toBe("2m 5s");
    expect(fmtDuration(null)).toBe("–");
    const now = Date.parse("2026-11-03T12:00:00Z");
    expect(ago("2026-11-03T11:55:00Z", now)).toBe("5min ago");
    expect(ago("2026-11-03T13:00:00Z", now)).toBe("in 1h");
    expect(ago(null)).toBe("–");
  });
  it("computes percentages safely", () => {
    expect(pct(1, 4)).toBe("25%");
    expect(pct(1, 0)).toBe("0%");
  });
});
