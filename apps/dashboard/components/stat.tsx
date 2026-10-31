import type { ReactNode } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Sign colour is reserved for performance figures: green/red never mean anything else. */
export function signClass(x: number | null): string {
  if (x === null || x === 0) return "";
  return x > 0 ? "text-emerald-400" : "text-red-400";
}

export function Stat({
  label,
  value,
  hint,
  valueClassName,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  valueClassName?: string;
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="text-xs font-normal uppercase tracking-wide text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <div className={cn("font-mono text-xl tabular-nums", valueClassName)}>{value}</div>
        {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

export function Alert({
  tone = "warning",
  title,
  children,
}: {
  tone?: "warning" | "critical";
  title: string;
  children?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-lg border p-3 text-sm",
        tone === "critical"
          ? "border-destructive/60 bg-destructive/10"
          : "border-amber-500/40 bg-amber-500/5",
      )}
    >
      <div className="font-medium">{title}</div>
      {children ? <div className="mt-1 text-muted-foreground">{children}</div> : null}
    </div>
  );
}
