import { describe, expect, it } from "vitest";
import { cycleErrorMessage } from "./run-cycle";

describe("cycleErrorMessage", () => {
  it("says the worker is active instead of the API's raw refusal", () => {
    expect(cycleErrorMessage("a worker is active on this database and runs the cycles itself")).toBe(
      "Worker actif : il lance déjà les cycles tout seul.",
    );
  });

  it("says a manual cycle is already running", () => {
    expect(cycleErrorMessage("a cycle is already running on this database")).toBe(
      "Un cycle est déjà en cours.",
    );
  });

  it("leaves any other error untouched", () => {
    expect(cycleErrorMessage("API injoignable")).toBe("API injoignable");
  });
});
