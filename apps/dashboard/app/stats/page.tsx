"use client";

import Link from "next/link";

import { DecisionJustification } from "@/components/decision-justification";
import { QueryState } from "@/components/state";
import { Alert, Stat, signClass } from "@/components/stat";
import { BetsPerDayChart, PnlCurveChart } from "@/components/stats-charts";
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
import { useDecisions, useStats } from "@/lib/api";
import {
  formatDateTime,
  formatMana,
  formatNumber,
  formatProb,
  formatSignedMana,
} from "@/lib/format";
import {
  betSampleNotice,
  formatProfitFactor,
  formatRateInterval,
  formatRecord,
  formatStreak,
  resultLabel,
} from "@/lib/stats";
import type { BetGroup, BetLine } from "@/lib/types";

const NUM = "text-right font-mono tabular-nums";

function GroupTable({ title, label, groups }: { title: string; label: string; groups: BetGroup[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{label}</TableHead>
              <TableHead className="text-right">Paris</TableHead>
              <TableHead className="text-right">G – P</TableHead>
              <TableHead className="text-right">Réussite</TableHead>
              <TableHead className="text-right">Misé</TableHead>
              <TableHead className="text-right">P&amp;L réalisé</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.map((g) => (
              <TableRow key={g.label}>
                <TableCell className="font-mono tabular-nums">{g.label}</TableCell>
                <TableCell className={NUM}>{formatNumber(g.bets, 0)}</TableCell>
                <TableCell className={NUM}>{formatRecord(g.won, g.lost)}</TableCell>
                <TableCell className={NUM}>{formatProb(g.win_rate)}</TableCell>
                <TableCell className={NUM}>{formatMana(g.staked)}</TableCell>
                <TableCell className={`${NUM} ${signClass(g.realized_pnl)}`}>
                  {formatSignedMana(g.realized_pnl)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function BetHint({ bet }: { bet: BetLine | null }) {
  if (bet === null) return null;
  return (
    <Link className="line-clamp-2 hover:underline" href={`/markets/${encodeURIComponent(bet.market_id)}`}>
      {bet.question ?? bet.market_id}
    </Link>
  );
}

function SettledTable({ bets }: { bets: BetLine[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Derniers paris réglés</CardTitle>
      </CardHeader>
      <CardContent>
        {bets.length === 0 ? (
          <p className="text-sm text-muted-foreground">Aucun pari réglé pour l&apos;instant.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Marché</TableHead>
                <TableHead>Côté</TableHead>
                <TableHead>Résolution</TableHead>
                <TableHead className="text-right">Prix d&apos;entrée</TableHead>
                <TableHead className="text-right">Mise</TableHead>
                <TableHead className="text-right">P&amp;L</TableHead>
                <TableHead>Résultat</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {bets.map((b) => (
                <TableRow key={b.market_id}>
                  <TableCell className="max-w-48 truncate" title={`Réglé le ${formatDateTime(b.settled_at)}`}>
                    <Link className="hover:underline" href={`/markets/${encodeURIComponent(b.market_id)}`}>
                      {b.question ?? b.market_id}
                    </Link>
                  </TableCell>
                  <TableCell>{b.side}</TableCell>
                  <TableCell>{b.resolution ?? "—"}</TableCell>
                  <TableCell className={NUM}>{formatProb(b.entry_price)}</TableCell>
                  <TableCell className={NUM}>{formatMana(b.stake)}</TableCell>
                  <TableCell className={`${NUM} ${signClass(b.pnl)}`}>{formatSignedMana(b.pnl)}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{resultLabel(b.pnl, b.resolution)}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function LatestJustifications() {
  const decisions = useDecisions({ limit: 6, offset: 0 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Ambre explique ses derniers choix</CardTitle>
      </CardHeader>
      <CardContent>
        <QueryState query={decisions} empty="Aucune décision pour l'instant.">
          {(page) => (
            <div className="flex flex-col divide-y">
              {page.items.map((d) => (
                <div key={d.decision_id} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 text-xs text-muted-foreground">
                    <Link className="hover:underline" href={`/markets/${encodeURIComponent(d.market_id)}`}>
                      {d.market_question ?? d.market_id}
                    </Link>
                    <span>{formatDateTime(d.created_at)}</span>
                  </div>
                  <DecisionJustification decision={d} />
                </div>
              ))}
            </div>
          )}
        </QueryState>
      </CardContent>
    </Card>
  );
}

export default function StatsPage() {
  const stats = useStats();

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Statistiques</h1>
        <p className="text-sm text-muted-foreground">
          Paris simulés de la simulation active, en mana virtuel. Un pari est une position réellement
          ouverte : les décisions refusées ne comptent pas.
        </p>
      </div>

      <QueryState query={stats} empty="Aucune simulation active : lancez `ambre cycle`.">
        {(s) => {
          const notice = betSampleNotice(s.won + s.lost, s.min_sample);
          return (
            <>
              {notice ? <Alert title="Résultats non concluants">{notice}</Alert> : null}

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Stat
                  label="Taux de réussite"
                  value={formatProb(s.win_rate)}
                  hint={`${formatRecord(s.won, s.lost)} · IC 95 % ${formatRateInterval(s.win_rate_ci95)}`}
                />
                <Stat
                  label="Paris"
                  value={formatNumber(s.bets, 0)}
                  hint={`${s.open} ouverts · ${s.settled} réglés · ${s.refunded} remboursés`}
                />
                <Stat
                  label="Total misé"
                  value={formatMana(s.total_staked)}
                  hint={`dont ${formatMana(s.open_stake)} encore en jeu`}
                />
                <Stat
                  label="P&L total"
                  value={formatSignedMana(s.total_pnl)}
                  valueClassName={signClass(s.total_pnl)}
                  hint={`réalisé ${formatSignedMana(s.realized_pnl)} · latent ${formatSignedMana(s.unrealized_pnl)}`}
                />
                <Stat
                  label="ROI réalisé"
                  value={formatProb(s.roi)}
                  valueClassName={signClass(s.roi)}
                  hint="P&L réalisé / mises réglées"
                />
                <Stat
                  label="Réussite attendue"
                  value={formatProb(s.model_implied_win_rate)}
                  hint={`par le modèle · par le prix du marché : ${formatProb(s.market_implied_win_rate)}`}
                />
                <Stat
                  label="Facteur de profit"
                  value={formatProfitFactor(s.profit_factor)}
                  hint="gains bruts / pertes brutes"
                />
                <Stat
                  label="Série en cours"
                  value={formatStreak(s.current_streak)}
                  hint={`record : ${s.longest_win_streak} gagnés · ${s.longest_loss_streak} perdus`}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Mise moyenne" value={formatMana(s.average_stake)} hint={`médiane ${formatMana(s.median_stake)}`} />
                <Stat
                  label="Plus grosse mise"
                  value={formatMana(s.largest_stake)}
                  hint={`plus petite : ${formatMana(s.smallest_stake)}`}
                />
                <Stat
                  label="Gain moyen"
                  value={formatSignedMana(s.average_win)}
                  valueClassName={signClass(s.average_win)}
                  hint={`sur ${s.won} pari${s.won > 1 ? "s" : ""} gagné${s.won > 1 ? "s" : ""}`}
                />
                <Stat
                  label="Perte moyenne"
                  value={formatSignedMana(s.average_loss)}
                  valueClassName={signClass(s.average_loss)}
                  hint={`pire : ${formatSignedMana(s.worst_bet?.pnl ?? null)}`}
                />
                <Stat label="Décisions" value={formatNumber(s.decisions.total, 0)} hint="journalisées" />
                <Stat label="Simulées" value={formatNumber(s.decisions.simulated, 0)} hint="paris ouverts" />
                <Stat label="Refusées" value={formatNumber(s.decisions.rejected, 0)} hint="par le moteur de risque" />
                <Stat label="Sans pari" value={formatNumber(s.decisions.no_trade, 0)} hint="pas d'avantage" />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>P&amp;L réalisé cumulé, pari par pari</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {s.pnl_curve.length === 0 ? (
                      <p className="text-sm text-muted-foreground">Aucun pari réglé pour l&apos;instant.</p>
                    ) : (
                      <PnlCurveChart points={s.pnl_curve} />
                    )}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>Paris ouverts par jour (UTC)</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {s.bets_per_day.length === 0 ? (
                      <p className="text-sm text-muted-foreground">Aucun pari pour l&apos;instant.</p>
                    ) : (
                      <BetsPerDayChart days={s.bets_per_day} />
                    )}
                  </CardContent>
                </Card>
              </div>

              <GroupTable title="Par côté" label="Côté" groups={s.by_side} />
              <GroupTable title="Par prix d'entrée" label="Prix payé" groups={s.by_entry_price} />

              <div className="grid gap-4 lg:grid-cols-2">
                <Stat label={(s.best_bet?.pnl ?? 0) < 0 ? "Plus petite perte" : "Meilleur pari"} value={formatSignedMana(s.best_bet?.pnl ?? null)} valueClassName={signClass(s.best_bet?.pnl ?? null)} hint={<BetHint bet={s.best_bet} />} />
                <Stat label="Pire pari" value={formatSignedMana(s.worst_bet?.pnl ?? null)} valueClassName={signClass(s.worst_bet?.pnl ?? null)} hint={<BetHint bet={s.worst_bet} />} />
              </div>

              <SettledTable bets={s.recent_settled} />
            </>
          );
        }}
      </QueryState>

      <LatestJustifications />
    </>
  );
}
