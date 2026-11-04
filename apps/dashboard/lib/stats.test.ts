import { describe, expect, it } from "vitest";

import {
  betSampleNotice,
  formatProfitFactor,
  formatRateInterval,
  formatRecord,
  formatStreak,
  resultLabel,
} from "./stats";

describe("betSampleNotice", () => {
  it("says so when no bet has a result yet", () => {
    expect(betSampleNotice(0, 30)).toContain("Aucun pari");
  });
  it("warns below the minimum sample", () => {
    expect(betSampleNotice(10, 30)).toBe(
      "Échantillon insuffisant : 10 paris gagnés ou perdus sur 30 minimum. Ne tirez aucune conclusion.",
    );
    expect(betSampleNotice(1, 30)).toContain("1 pari gagné ou perdu");
  });
  it("stays silent once the sample is large enough", () => {
    expect(betSampleNotice(30, 30)).toBeNull();
  });
});

describe("formatRecord", () => {
  it("renders wins and losses", () => {
    expect(formatRecord(3, 7)).toBe("3 G – 7 P");
  });
});

describe("formatStreak", () => {
  it("names the run", () => {
    expect(formatStreak(3)).toBe("3 gagnés");
    expect(formatStreak(1)).toBe("1 gagné");
    expect(formatStreak(-10)).toBe("10 perdus");
    expect(formatStreak(-1)).toBe("1 perdu");
  });
  it("renders no run as a dash, not 0", () => {
    expect(formatStreak(0)).toBe("—");
  });
});

describe("formatProfitFactor", () => {
  it("renders null as a dash, never 0", () => {
    expect(formatProfitFactor(null)).toBe("—");
  });
  it("keeps a real zero", () => {
    expect(formatProfitFactor(0)).toBe("0.00");
  });
});

describe("resultLabel", () => {
  it("judges on the money", () => {
    expect(resultLabel(5, "YES")).toBe("Gagné");
    expect(resultLabel(-5, "NO")).toBe("Perdu");
    expect(resultLabel(0, "MKT")).toBe("Neutre");
  });
  it("names a refund", () => {
    expect(resultLabel(0, "CANCEL")).toBe("Remboursé");
  });
});

describe("formatRateInterval", () => {
  it("renders percentages", () => {
    expect(formatRateInterval([0, 0.2775])).toBe("[0.0 % ; 27.8 %]");
  });
  it("renders null as a dash", () => {
    expect(formatRateInterval(null)).toBe("—");
  });
});
