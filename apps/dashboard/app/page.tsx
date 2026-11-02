"use client";

import { CapitalChart } from "@/components/capital-chart";
import { KpiCard } from "@/components/kpi";
import { QueryState } from "@/components/state";
import { Alert, Stat, signClass } from "@/components/stat";
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
import { usePortfolio, usePortfolioHistory, useSystem } from "@/lib/api";
import {
  formatDateTime,
  formatDrawdown,
  formatMana,
  formatNumber,
  formatRelativeTime,
  formatSignedMana,
} from "@/lib/format";
import type { Portfolio, SystemStatus } from "@/lib/types";
import { sparkSeries } from "@/lib/spark";
import { useNow } from "@/lib/use-now";
import Link from "next/link";

function Alerts({ portfolio, system }: { portfolio: Portfolio | null; system: SystemStatus | undefined }) {
  const now = useNow();
  const items: React.ReactNode[] = [];
  if (system?.halted) {
    items.push(
      <Alert key="halt" tone="critical" title="Arrêt d'urgence actif (HALTED)">
        {system.halt_reason ?? "Aucun motif renseigné."} Aucune nouvelle mise n&apos;est simulée.
      </Alert>,
    );
  }
  if (portfolio?.simulation.status === "DEAD") {
    items.push(
      <Alert key="dead" tone="critical" title="Simulation morte">
        La valeur a atteint le seuil d&apos;échec. Elle n&apos;est jamais réinitialisée : créez une
        nouvelle simulation (`ambre new-simulation`).
      </Alert>,
    );
  }
  if (system && system.data_age_seconds !== null && system.data_age_seconds > system.survival.max_data_age_seconds) {
    items.push(
      <Alert key="stale" title="Données périmées">
        Dernière synchro {formatRelativeTime(system.last_sync_at, now)} (limite{" "}
        {system.survival.max_data_age_seconds} s). Le système observe sans miser.
      </Alert>,
    );
  }
  if (system && system.last_sync_at === null) {
    items.push(
      <Alert key="nosync" title="Aucune synchronisation">
        Lancez `ambre sync` ou `ambre worker`.
      </Alert>,
    );
  }
  if (items.length === 0) return null;
  return <div className="flex flex-col gap-2">{items}</div>;
}

export default function OverviewPage() {
  const now = useNow();
  const portfolio = usePortfolio();
  const history = usePortfolioHistory();
  const system = useSystem();

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Vue d&apos;ensemble</h1>
        <p className="text-sm text-muted-foreground">
          Simulation papier en Mana virtuel. Mana ≠ argent réel ; aucun résultat n&apos;indique une
          rentabilité.
        </p>
      </div>

      {system.isError ? null : <Alerts portfolio={portfolio.data ?? null} system={system.data} />}

      <QueryState
        query={portfolio}
        empty={
          <>
            Aucune simulation. Initialisez la base (`ambre init-db`) puis lancez `ambre cycle` ou{" "}
            `ambre worker`.
          </>
        }
      >
        {(p) => {
          const performance = p.total_value - p.simulation.initial_capital;
          const bound = p.total_value_is_lower_bound;
          return (
            <>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                <KpiCard
                  id="total"
                  label="Capital total"
                  value={`${bound ? "≥ " : ""}${formatMana(p.total_value)}`}
                  hint={bound ? "Borne basse : exposition inconnue" : `Initial ${formatMana(p.simulation.initial_capital)}`}
                  values={sparkSeries(history.data ?? [], (x) => x.at, (x) => x.total_value)}
                />
                <KpiCard
                  id="cash"
                  label="Cash"
                  value={formatMana(p.cash)}
                  values={sparkSeries(history.data ?? [], (x) => x.at, (x) => x.cash)}
                />
                <KpiCard
                  id="latent"
                  label="Valeur latente"
                  value={formatMana(p.positions_value)}
                  hint="latente, non réalisée"
                  values={sparkSeries(history.data ?? [], (x) => x.at, (x) => x.positions_value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                <Stat
                  label="Performance nette"
                  value={bound ? "—" : formatSignedMana(performance)}
                  valueClassName={bound ? undefined : signClass(performance)}
                  hint="total − capital initial"
                />
                <Stat
                  label="Drawdown"
                  value={formatDrawdown(p.drawdown)}
                  hint={`Pic ${formatMana(p.peak_value)}`}
                />
                <Stat
                  label="État de survie"
                  value={p.survival_state ?? "—"}
                  hint={p.survival_reasons.length ? p.survival_reasons.join(" · ") : undefined}
                />
                <Stat label="Positions ouvertes" value={formatNumber(p.open_positions.length, 0)} />
                <Stat
                  label="Stratégie active"
                  value={system.data?.active_strategy ?? "—"}
                  hint={
                    system.data
                      ? `${formatNumber(system.data.decisions_total, 0)} décisions · dernier cycle ${formatRelativeTime(system.data.last_cycle_at, now)}`
                      : undefined
                  }
                />
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Valeur totale</CardTitle>
                </CardHeader>
                <CardContent>
                  <QueryState
                    query={history}
                    empty="Pas encore d'historique : il apparaît après le premier cycle."
                  >
                    {(h) => <CapitalChart history={h} initialCapital={p.simulation.initial_capital} />}
                  </QueryState>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Positions ouvertes</CardTitle>
                </CardHeader>
                <CardContent>
                  {p.open_positions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Aucune position ouverte.</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Marché</TableHead>
                          <TableHead>Côté</TableHead>
                          <TableHead className="text-right">Parts</TableHead>
                          <TableHead className="text-right">Coût</TableHead>
                          <TableHead className="text-right">Valeur latente</TableHead>
                          <TableHead>Ouverte</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {p.open_positions.map((pos) => (
                          <TableRow key={`${pos.market_id}-${pos.side}`}>
                            <TableCell className="max-w-md truncate">
                              <Link className="hover:underline" href={`/markets/${encodeURIComponent(pos.market_id)}`}>
                                {pos.question ?? pos.market_id}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">{pos.side}</Badge>
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {formatNumber(pos.shares)}
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {formatMana(pos.cost)}
                            </TableCell>
                            <TableCell className="text-right font-mono tabular-nums">
                              {formatMana(pos.current_value)}
                            </TableCell>
                            <TableCell title={formatDateTime(pos.opened_at)}>
                              {formatRelativeTime(pos.opened_at, now)}
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
