import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
const declarations = [...css.matchAll(/(--radius[\w-]*):\s*([^;]+);/g)].map(
  ([, name, value]) => ({ name, value: value.trim() }),
);

describe("border radius", () => {
  it("declares the radius scale", () => {
    expect(declarations.map((d) => d.name)).toEqual(
      expect.arrayContaining(["--radius", "--radius-sm", "--radius-md", "--radius-lg", "--radius-4xl"]),
    );
  });

  it("never exceeds 5px, in plain pixels (no calc/rem that could scale past the cap)", () => {
    for (const { name, value } of declarations) {
      expect(value, name).toMatch(/^\d+(\.\d+)?px$/);
      expect(parseFloat(value), name).toBeLessThanOrEqual(5);
    }
  });
});
