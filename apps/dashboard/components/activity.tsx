"use client";

import Link from "next/link";

import { useDecisions } from "@/lib/api";
import { formatMana, formatRelativeTime } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { Decision } from "@/lib/types";

const LIMIT = 25;

function actionClass(action: string): string {
  if (action === "YES") return "text-emerald-400";
  if (action === "NO") return "text-red-400";
  return "text-muted-foreground";
}

function Item({ d, now }: { d: Decision; now: Date | null }) {
  const placed = d.execution_status === "SIMULATED";
  return (
    <li className="border-b py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className={cn("font-mono font-medium", actionClass(d.action))}>
          {d.action}
          <span className="ml-2 font-sans font-normal text-muted-foreground">{d.actor}</span>
        </span>
        <span className="text-muted-foreground">{formatRelativeTime(d.created_at, now)}</span>
      </div>
      <Link
        href={`/markets/${encodeURIComponent(d.market_id)}`}
        className="mt-1 line-clamp-2 text-sm hover:underline"
      >
        {d.market_question ?? d.market_id}
      </Link>
      <div className="mt-1 text-xs text-muted-foreground">
        {placed ? `Mise simulée ${formatMana(d.stake)}` : `Non placée (${d.execution_status})`}
      </div>
    </li>
  );
}

/** Right-hand feed of Ambre's latest decisions, refreshed with the app's polling interval. */
export function ActivityPanel() {
  const decisions = useDecisions({ limit: LIMIT, offset: 0 });
  const now = useNow();
  const items = [...(decisions.data?.items ?? [])].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
  return (
    <aside
      aria-label="Historique des actions d'Ambre"
      className="sticky top-0 hidden h-screen w-72 shrink-0 flex-col border-l bg-sidebar xl:flex"
    >
      <div className="flex items-baseline justify-between border-b px-4 py-4">
        <h2 className="text-sm font-semibold">Actions d&apos;Ambre</h2>
        {decisions.data ? (
          <span className="text-xs text-muted-foreground">{decisions.data.total} au total</span>
        ) : null}
      </div>
      <div className="flex-1 overflow-y-auto px-4">
        {decisions.isPending ? (
          <p className="py-4 text-sm text-muted-foreground">Chargement…</p>
        ) : decisions.isError ? (
          <p role="alert" className="py-4 text-sm text-destructive">
            Historique indisponible : {decisions.error.message}
          </p>
        ) : items.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            Aucune décision. Lancez `ambre cycle` ou attendez le worker.
          </p>
        ) : (
          <ul>
            {items.map((d) => (
              <Item key={d.decision_id} d={d} now={now} />
            ))}
          </ul>
        )}
      </div>
      <div className="border-t px-4 py-3 text-xs">
        <Link href="/journal" className="text-muted-foreground hover:text-foreground">
          Journal complet →
        </Link>
      </div>
    </aside>
  );
}
