"use client";

import { Button } from "@/components/ui/button";
import { useRunCycle } from "@/lib/api";

/** Runs one paper cycle on the stored data; the worker keeps syncing and cycling on its own. */
export function RunCycleButton() {
  const run = useRunCycle();
  const r = run.data;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant="outline" disabled={run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? "Cycle en cours…" : "Lancer un cycle"}
      </Button>
      {run.isError ? (
        <span role="alert" className="max-w-xs text-right text-xs text-destructive">
          {run.error.message}
        </span>
      ) : r ? (
        <span role="status" className="text-xs text-muted-foreground">
          {r.simulated} simulée(s) · {r.rejected} refusée(s) · {r.skipped_no_capacity} sans capacité
        </span>
      ) : null}
    </div>
  );
}
