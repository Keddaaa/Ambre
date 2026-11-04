"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatNumber, formatSignedMana } from "@/lib/format";
import type { DayCount, PnlPoint } from "@/lib/types";

const AXIS = { fontSize: 11, fill: "var(--muted-foreground)" };
const TOOLTIP = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
};

/** Cumulative realized P&L, one point per settled bet: many bets settle in the same instant. */
export function PnlCurveChart({ points }: { points: PnlPoint[] }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="index" type="number" domain={["dataMin", "dataMax"]} allowDecimals={false} tick={AXIS} stroke="var(--border)" />
          <YAxis tickFormatter={(v) => formatNumber(Number(v), 0)} tick={AXIS} width={56} stroke="var(--border)" />
          <Tooltip
            labelFormatter={(i) => `Pari réglé n° ${i}`}
            formatter={(v) => [formatSignedMana(Number(v)), "P&L cumulé"]}
            contentStyle={TOOLTIP}
          />
          <ReferenceLine y={0} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
          <Line type="linear" dataKey="cumulative_pnl" stroke="var(--chart-1)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function BetsPerDayChart({ days }: { days: DayCount[] }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={days} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="day" tickFormatter={(d) => String(d).slice(5)} tick={AXIS} stroke="var(--border)" />
          <YAxis allowDecimals={false} tick={AXIS} width={40} stroke="var(--border)" />
          <Tooltip formatter={(v) => [formatNumber(Number(v), 0), "Paris"]} contentStyle={TOOLTIP} cursor={{ fill: "var(--muted)" }} />
          <Bar dataKey="bets" fill="var(--chart-1)" maxBarSize={48} radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
