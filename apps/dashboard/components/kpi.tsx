"use client";

import type { ReactNode } from "react";
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function Sparkline({ values, id }: { values: number[]; id: string }) {
  if (values.length < 2) return <div aria-hidden className="h-12 w-28" />;
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div aria-hidden className="h-12 w-28">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area
            type="monotone"
            dataKey="v"
            stroke="var(--chart-1)"
            strokeWidth={1.25}
            fill={`url(#${id})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Headline figure with a sparkline of its history; the sparkline is blank when there is no history. */
export function KpiCard({
  id,
  label,
  value,
  hint,
  values,
  valueClassName,
}: {
  id: string;
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  values: number[];
  valueClassName?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-end justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
          <span className={cn("truncate font-mono text-2xl tabular-nums", valueClassName)}>
            {value}
          </span>
          {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
        </div>
        <Sparkline values={values} id={`spark-${id}`} />
      </CardContent>
    </Card>
  );
}
