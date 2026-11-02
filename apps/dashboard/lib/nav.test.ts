import { describe, expect, it } from "vitest";

import { NAV_SECTIONS, activeSection, isActive } from "./nav";

describe("isActive", () => {
  it("matches the root only exactly", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/", "/markets")).toBe(false);
  });

  it("matches a section page and its sub-pages, not a lookalike prefix", () => {
    expect(isActive("/markets", "/markets")).toBe(true);
    expect(isActive("/markets", "/markets/abc")).toBe(true);
    expect(isActive("/markets", "/markets-old")).toBe(false);
  });
});

describe("activeSection", () => {
  it("finds the section owning the current page", () => {
    expect(activeSection("/").id).toBe("pilotage");
    expect(activeSection("/markets/abc").id).toBe("pilotage");
    expect(activeSection("/evaluation").id).toBe("analyse");
    expect(activeSection("/settings").id).toBe("systeme");
  });

  it("falls back to the first section for an unknown path", () => {
    expect(activeSection("/nope").id).toBe(NAV_SECTIONS[0].id);
  });

  it("lists every page exactly once", () => {
    const hrefs = NAV_SECTIONS.flatMap((s) => s.links.map((l) => l.href));
    expect(new Set(hrefs).size).toBe(hrefs.length);
    expect(hrefs).toEqual(["/", "/markets", "/journal", "/evaluation", "/settings"]);
  });
});
