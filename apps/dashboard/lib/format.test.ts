import { describe, expect, it } from "vitest";

import {
  formatDateTime,
  formatDrawdown,
  formatMana,
  formatNumber,
  formatProb,
  formatRelativeTime,
  formatSignedMana,
  formatSignedNumber,
} from "./format";

describe("formatProb", () => {
  it("renders null as an em dash, never 0", () => {
    expect(formatProb(null)).toBe("—");
  });
  it("renders a probability as a percentage with one decimal", () => {
    expect(formatProb(0.61)).toBe("61.0 %");
    expect(formatProb(0)).toBe("0.0 %");
    expect(formatProb(1)).toBe("100.0 %");
  });
});

describe("formatMana", () => {
  it("groups thousands with a space and keeps two decimals", () => {
    expect(formatMana(1000)).toBe("M 1 000.00");
    expect(formatMana(1234567.891)).toBe("M 1 234 567.89");
    expect(formatMana(0)).toBe("M 0.00");
    expect(formatMana(12.5)).toBe("M 12.50");
  });
  it("uses a typographic minus for negatives", () => {
    expect(formatMana(-1500)).toBe("M −1 500.00");
  });
  it("renders null as an em dash", () => {
    expect(formatMana(null)).toBe("—");
  });
});

describe("formatSignedMana", () => {
  it("always shows the sign", () => {
    expect(formatSignedMana(12.3)).toBe("+M 12.30");
    expect(formatSignedMana(-1200)).toBe("−M 1 200.00");
    expect(formatSignedMana(0)).toBe("M 0.00");
    expect(formatSignedMana(null)).toBe("—");
  });
});

describe("formatDrawdown", () => {
  it("renders a drawdown fraction as a negative percentage", () => {
    expect(formatDrawdown(0.123)).toBe("−12.3 %");
  });
  it("renders zero drawdown without a sign", () => {
    expect(formatDrawdown(0)).toBe("0.0 %");
  });
  it("renders null (unknown exposure) as an em dash", () => {
    expect(formatDrawdown(null)).toBe("—");
  });
});

describe("formatNumber / formatSignedNumber", () => {
  it("formats with fixed decimals and dashes for null", () => {
    expect(formatNumber(0.04567, 3)).toBe("0.046");
    expect(formatNumber(null, 2)).toBe("—");
    expect(formatNumber(-0.5, 2)).toBe("−0.50");
    expect(formatSignedNumber(0.0412, 3)).toBe("+0.041");
    expect(formatSignedNumber(-0.0412, 3)).toBe("−0.041");
    expect(formatSignedNumber(null, 3)).toBe("—");
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-10-31T12:00:00Z");
  it("handles recent, minutes, hours and days in the past", () => {
    expect(formatRelativeTime("2026-10-31T11:59:30Z", now)).toBe("à l'instant");
    expect(formatRelativeTime("2026-10-31T11:55:00Z", now)).toBe("il y a 5 min");
    expect(formatRelativeTime("2026-10-31T09:00:00Z", now)).toBe("il y a 3 h");
    expect(formatRelativeTime("2026-10-29T12:00:00Z", now)).toBe("il y a 2 j");
  });
  it("handles future times", () => {
    expect(formatRelativeTime("2026-10-31T12:10:00Z", now)).toBe("dans 10 min");
    expect(formatRelativeTime("2026-11-03T12:00:00Z", now)).toBe("dans 3 j");
  });
  it("treats API datetimes without offset as UTC", () => {
    expect(formatRelativeTime("2026-10-31T11:55:00", now)).toBe("il y a 5 min");
  });
  it("renders null or invalid input as an em dash", () => {
    expect(formatRelativeTime(null, now)).toBe("—");
    expect(formatRelativeTime("not a date", now)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("renders an ISO datetime as UTC YYYY-MM-DD HH:MM", () => {
    expect(formatDateTime("2026-10-31T09:05:00Z")).toBe("2026-10-31 09:05 UTC");
    expect(formatDateTime("2026-10-31T09:05:00")).toBe("2026-10-31 09:05 UTC");
    expect(formatDateTime(null)).toBe("—");
  });
});
