import { describe, expect, it } from "vitest";
import { formatClock, formatDuration, formatFigure, formatPeriodChip, shortId } from "../src/lib/format";

describe("formatFigure", () => {
  it("scales dollar amounts", () => {
    expect(formatFigure(94930000000, "USD")).toBe("$94.93B");
    expect(formatFigure(383266000000, "USD")).toBe("$383.27B");
    expect(formatFigure(-29789000000, "USD")).toBe("-$29.79B");
    expect(formatFigure(4200000, "USD")).toBe("$4.20M");
  });
  it("renders per-share values", () => {
    expect(formatFigure(2.02, "USD/shares")).toBe("$2.02/share");
  });
  it("keeps small unitless numbers readable", () => {
    expect(formatFigure(1234.567, null)).toBe("1,234.57");
  });
  it("never renders a missing value as a number", () => {
    expect(formatFigure(null, "USD")).toBe("—");
    expect(formatFigure(Number.NaN, "USD")).toBe("—");
  });
});

describe("period and time", () => {
  it("builds the filer's fiscal label", () => {
    expect(formatPeriodChip({ fy: 2026, fp: "Q3", form: "10-Q" })).toBe("Q3 FY26 · 10-Q");
    expect(formatPeriodChip({ fy: 2025, fp: "FY", form: "10-K" })).toBe("FY25 · 10-K");
    expect(formatPeriodChip({})).toBe("");
  });
  it("formats durations", () => {
    expect(formatDuration(36859)).toBe("36.9s");
    expect(formatDuration(128)).toBe("128ms");
    expect(formatDuration(null)).toBe("");
  });
  it("formats clock times and tolerates bad input", () => {
    expect(formatClock("not a date")).toBe("--:--:--.---");
    expect(formatClock(undefined)).toBe("--:--:--.---");
    expect(formatClock("2026-09-24T16:03:01.103+00:00")).toMatch(/^\d{2}:\d{2}:\d{2}\.103$/);
  });
  it("shortens ids", () => {
    expect(shortId("7f64e398-30a0-4eec-a6dd-07ee94481daa")).toBe("7f64e398…");
  });
});
