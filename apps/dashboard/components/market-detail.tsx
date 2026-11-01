"use client";

import Link from "next/link";

import { ProbabilityChart } from "@/components/capital-chart";
import { QueryState } from "@/components/state";
import { Stat } from "@/components/stat";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMarket } from "@/lib/api";
import { formatDateTime, formatEstimate, formatMana, formatNumber, formatProb } from "@/lib/format";

export function MarketDetail({ id }: { id: string }) {
  const detail = useMarket(id);

  return (
    <>
      <Link href="/markets" className="text-sm text-muted-foreground hover:text-foreground">
        ← Marchés
      </Link>
      <QueryState query={detail} empty="Marché introuvable.">
        {({ market, snapshots, decisions }) => {
          const last = snapshots.reduce<(typeof snapshots)[number] | null>(
            (acc, s) => (acc === null || s.observed_at > acc.observed_at ? s : acc),
            null,
          );
          return (
            <>
              <div className="flex flex-col gap-2">
                <h1 className="text-lg font-semibold">{market.question}</h1>
                <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                  <span>par {market.creator_username}</span>
                  <span>· clôture {formatDateTime(market.close_at)}</span>
                  {market.is_resolved ? <Badge variant="secondary">résolu : {market.resolution ?? "?"}</Badge> : null}
                  <a
                    href={market.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline underline-offset-4 hover:text-foreground"
                  >
                    Voir sur Manifold
                  </a>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Probabilité marché" value={formatProb(last?.probability ?? null)} />
                <Stat label="Liquidité" value={formatNumber(last?.total_liquidity ?? null, 0)} />
                <Stat label="Parieurs" value={formatNumber(last?.unique_bettor_count ?? null, 0)} />
                <Stat label="Snapshots" value={formatNumber(snapshots.length, 0)} />
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Probabilité observée</CardTitle>
                </CardHeader>
                <CardContent>
                  {snapshots.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Aucun snapshot.</p>
                  ) : (
                    <ProbabilityChart snapshots={snapshots} />
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Décisions associées</CardTitle>
                </CardHeader>
                <CardContent>
                  {decisions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Aucune décision sur ce marché.</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Date</TableHead>
                          <TableHead>Action</TableHead>
                          <TableHead className="text-right">Mise</TableHead>
                          <TableHead className="text-right">Marché</TableHead>
                          <TableHead className="text-right">Estimée ± inc.</TableHead>
                          <TableHead>Exécution</TableHead>
                          <TableHead>Raisons</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {decisions.map((d) => (
                          <TableRow key={d.decision_id}>
                            <TableCell>{formatDateTime(d.created_at)}</TableCell>
                            <TableCell>
                              <Badge variant="outline">{d.action}</Badge>
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {d.execution_status === "SIMULATED" ? formatMana(d.stake) : "—"}
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {formatProb(d.market_probability)}
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {formatEstimate(d.estimated_probability, d.uncertainty)}
                            </TableCell>
                            <TableCell>{d.execution_status}</TableCell>
                            <TableCell className="max-w-xs truncate" title={d.reasons.join(" · ")}>
                              {d.reasons.join(" · ") || "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
