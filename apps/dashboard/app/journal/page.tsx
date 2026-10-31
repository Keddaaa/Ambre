"use client";

import Link from "next/link";
import { useState } from "react";

import { Pager, useClampOffset } from "@/components/pager";
import { QueryState } from "@/components/state";
import { signClass } from "@/components/stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDecisions } from "@/lib/api";
import { formatDateTime, formatNumber, formatProb, formatSignedMana } from "@/lib/format";

const LIMIT = 50;
const ACTIONS: { value: string | undefined; label: string }[] = [
  { value: undefined, label: "Toutes" },
  { value: "YES", label: "YES" },
  { value: "NO", label: "NO" },
  { value: "NO_TRADE", label: "NO_TRADE" },
];

export default function JournalPage() {
  const [action, setAction] = useState<string | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const decisions = useDecisions({ action, limit: LIMIT, offset });
  useClampOffset(decisions.data?.total, offset, LIMIT, setOffset);
  // keepPreviousData keeps the old rows while a new filter loads: make that visible.
  const stale = decisions.isPlaceholderData;

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Journal des décisions</h1>
        <p className="text-sm text-muted-foreground">
          Chaque décision est journalisée, y compris les refus. Une mise proposée n&apos;est placée
          que si l&apos;exécution est SIMULATED.
        </p>
      </div>

      <div className="flex gap-1" role="group" aria-label="Action">
        {ACTIONS.map((a) => (
          <Button
            key={a.label}
            size="sm"
            variant={action === a.value ? "default" : "outline"}
            onClick={() => {
              setAction(a.value);
              setOffset(0);
            }}
          >
            {a.label}
          </Button>
        ))}
      </div>

      <QueryState query={decisions} empty="Aucune décision. Lancez `ambre cycle`.">
        {(page) => (
          <div
            className={`flex flex-col gap-3 transition-opacity ${stale ? "pointer-events-none opacity-50" : ""}`}
            aria-busy={stale}
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Marché</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead className="text-right">Mise</TableHead>
                  <TableHead className="text-right">Marché</TableHead>
                  <TableHead className="text-right">Estimée ± inc.</TableHead>
                  <TableHead className="text-right">Conservatrice</TableHead>
                  <TableHead className="text-right">EV</TableHead>
                  <TableHead>Survie</TableHead>
                  <TableHead>Exécution</TableHead>
                  <TableHead className="text-right">Résultat</TableHead>
                  <TableHead>Raisons</TableHead>
                  <TableHead>Refus</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((d) => {
                  const placed = d.execution_status === "SIMULATED";
                  const pnl = d.payout !== null ? d.payout - d.stake : null;
                  return (
                    <TableRow key={d.decision_id}>
                      <TableCell>{formatDateTime(d.created_at)}</TableCell>
                      <TableCell className="max-w-xs truncate">
                        <Link className="hover:underline" href={`/markets/${encodeURIComponent(d.market_id)}`}>
                          {d.market_question ?? d.market_id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{d.action}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {placed ? formatNumber(d.stake) : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatProb(d.market_probability)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatProb(d.estimated_probability)} ± {formatProb(d.uncertainty)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatProb(d.conservative_probability)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatNumber(d.ev_per_mana, 3)}
                      </TableCell>
                      <TableCell>{d.survival_state}</TableCell>
                      <TableCell>{d.execution_status}</TableCell>
                      <TableCell className={`text-right font-mono tabular-nums ${signClass(pnl)}`}>
                        {d.outcome === null ? "—" : `${d.outcome} ${formatSignedMana(pnl)}`}
                      </TableCell>
                      <TableCell className="max-w-xs truncate" title={d.reasons.join(" · ")}>
                        {d.reasons.join(" · ") || "—"}
                      </TableCell>
                      <TableCell className="max-w-xs truncate" title={d.risk_reasons.join(" · ")}>
                        {d.risk_reasons.join(" · ") || "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <Pager offset={offset} limit={LIMIT} total={page.total} onChange={setOffset} />
          </div>
        )}
      </QueryState>
    </>
  );
}
