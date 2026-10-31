"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDateTime, formatMana, formatProb, parseApiDate } from "@/lib/format";
import type { PortfolioPoint, Snapshot } from "@/lib/types";

interface Point {
  t: number;
  v: number;
}

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" };

function shortDate(t: number): string {
  return new Date(t).toISOString().slice(5, 16).replace("T", " ");
}

function TimeSeries({
  points,
  format,
  reference,
  referenceLabel,
  yDomain,
  showDots,
}: {
  points: Point[];
  format: (v: number) => string;
  reference?: number;
  referenceLabel?: string;
  yDomain: [number | "auto", number | "auto"];
  showDots?: boolean;
}) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={shortDate}
            tick={AXIS}
            stroke="var(--border)"
          />
          <YAxis domain={yDomain} tickFormatter={format} tick={AXIS} width={84} stroke="var(--border)" />
          <Tooltip
            labelFormatter={(t) => formatDateTime(new Date(Number(t)).toISOString())}
            formatter={(v) => [format(Number(v)), ""]}
            contentStyle={{
              background: "var(--popover)",
              border: "1px solid var(--border)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
            }}
          />
          {reference !== undefined ? (
            <ReferenceLine
              y={reference}
              stroke="var(--muted-foreground)"
              strokeDasharray="4 4"
              label={{ value: referenceLabel, position: "insideTopLeft", ...AXIS }}
            />
          ) : null}
          <Line
            type="linear"
            dataKey="v"
            stroke="var(--chart-1)"
            strokeWidth={1.5}
            dot={showDots ? { r: 2 } : false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function toPoints<T>(rows: T[], at: (r: T) => string, value: (r: T) => number): Point[] {
  const out: Point[] = [];
  for (const r of rows) {
    const d = parseApiDate(at(r));
    if (d !== null) out.push({ t: d.getTime(), v: value(r) });
  }
  return out;
}

/** Total value over portfolio snapshots, with the initial capital as reference line. */
export function CapitalChart({
  history,
  initialCapital = 1000,
}: {
  history: PortfolioPoint[];
  initialCapital?: number;
}) {
  return (
    <TimeSeries
      points={toPoints(history, (p) => p.at, (p) => p.total_value)}
      format={formatMana}
      reference={initialCapital}
      referenceLabel={`capital initial ${formatMana(initialCapital)}`}
      yDomain={["auto", "auto"]}
      showDots={history.length < 40}
    />
  );
}

/** Observed market probability, one dot per snapshot dated by observed_at. */
export function ProbabilityChart({ snapshots }: { snapshots: Snapshot[] }) {
  return (
    <TimeSeries
      points={toPoints(snapshots, (s) => s.observed_at, (s) => s.probability)}
      format={formatProb}
      yDomain={[0, 1]}
      showDots
    />
  );
}
