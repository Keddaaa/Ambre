import { describe, expect, it } from "vitest";

import { parseOrderForm } from "./order";

const base = { marketId: "m1", side: "YES" as const, probabilityPct: "62", stake: "10", note: "" };

describe("parseOrderForm", () => {
  it("converts the percentage to a P(YES) in (0, 1) and keeps the stake", () => {
    const r = parseOrderForm(base, "req-12345678");
    expect(r).toEqual({
      ok: true,
      order: {
        market_id: "m1",
        side: "YES",
        probability: 0.62,
        stake: 10,
        actor: "USER",
        request_id: "req-12345678",
        note: null,
      },
    });
  });

  it("accepts a decimal comma and trims the note", () => {
    const r = parseOrderForm({ ...base, probabilityPct: "62,5", note: "  vu  " }, "req-12345678");
    expect(r.ok && r.order.probability).toBe(0.625);
    expect(r.ok && r.order.note).toBe("vu");
  });

  it.each(["", "abc", "0", "100", "-5", "120", "NaN"])("refuses probability %j", (v) => {
    const r = parseOrderForm({ ...base, probabilityPct: v }, "req-12345678");
    expect(r.ok).toBe(false);
  });

  it.each(["", "abc", "0", "-3", "Infinity"])("refuses stake %j", (v) => {
    const r = parseOrderForm({ ...base, stake: v }, "req-12345678");
    expect(r.ok).toBe(false);
  });
});
