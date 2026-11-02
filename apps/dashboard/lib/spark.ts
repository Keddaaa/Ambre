import { parseApiDate } from "./format";

/** Values in chronological order; rows without a valid date or a finite value are dropped, never defaulted. */
export function sparkSeries<T>(rows: T[], at: (r: T) => string, value: (r: T) => number): number[] {
  const points: { t: number; v: number }[] = [];
  for (const r of rows) {
    const d = parseApiDate(at(r));
    const v = value(r);
    if (d !== null && Number.isFinite(v)) points.push({ t: d.getTime(), v });
  }
  return points.sort((a, b) => a.t - b.t).map((p) => p.v);
}
