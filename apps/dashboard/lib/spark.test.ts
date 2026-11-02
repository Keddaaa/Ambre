import { describe, expect, it } from "vitest";

import { sparkSeries } from "./spark";

const rows = [
  { at: "2026-11-02T10:00:00Z", v: 3 },
  { at: "2026-11-02T08:00:00Z", v: 1 },
  { at: "not a date", v: 99 },
  { at: "2026-11-02T09:00:00Z", v: Number.NaN },
  { at: "2026-11-02T09:30:00Z", v: 2 },
];

describe("sparkSeries", () => {
  it("orders by time and drops undated or non-finite points", () => {
    expect(
      sparkSeries(
        rows,
        (r) => r.at,
        (r) => r.v,
      ),
    ).toEqual([1, 2, 3]);
  });

  it("returns an empty series for no rows", () => {
    expect(
      sparkSeries(
        [] as typeof rows,
        (r) => r.at,
        (r) => r.v,
      ),
    ).toEqual([]);
  });
});
