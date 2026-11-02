"use client";

import { QueryState } from "@/components/state";
import { Alert, Stat, signClass } from "@/components/stat";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useEvaluation } from "@/lib/api";
import { formatInterval, formatNumber, formatProb, formatSignedNumber, sampleNotice } from "@/lib/format";
import type { CalibrationBin } from "@/lib/types";

function CalibrationTable({ title, bins }: { title: string; bins: CalibrationBin[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {bins.length === 0 ? (
          <p className="text-sm text-muted-foreground">Aucune tranche : pas de marché scoré.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tranche</TableHead>
                <TableHead className="text-right">Marchés</TableHead>
                <TableHead className="text-right">Prévu (moy.)</TableHead>
                <TableHead className="text-right">Observé</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {bins.map((b) => (
                <TableRow key={b.lower}>
                  <TableCell className="font-mono tabular-nums">
                    {formatProb(b.lower)} – {formatProb(b.upper)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{formatNumber(b.n, 0)}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{formatProb(b.mean_forecast)}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{formatProb(b.observed_rate)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export default function EvaluationPage() {
  const evaluation = useEvaluation();

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Évaluation</h1>
        <p className="text-sm text-muted-foreground">
          Score du modèle et du prix du marché sur les marchés résolus après la première décision
          d&apos;Ambre. Plus bas = mieux. Une seule observation par marché.
        </p>
      </div>

      <QueryState query={evaluation} empty="Aucune simulation active : lancez `ambre cycle`.">
        {(e) => {
          const notice = sampleNotice(e.n, e.min_sample);
          return (
            <>
              {notice ? (
                <Alert title="Résultats non concluants">{notice}</Alert>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Marchés scorés" value={formatNumber(e.n, 0)} hint={`minimum ${e.min_sample}`} />
                <Stat
                  label="Brier modèle"
                  value={formatNumber(e.model_brier, 4)}
                  hint={`prix du marché : ${formatNumber(e.market_brier, 4)}`}
                />
                <Stat
                  label="Log loss modèle"
                  value={formatNumber(e.model_log_loss, 4)}
                  hint={`prix du marché : ${formatNumber(e.market_log_loss, 4)}`}
                />
                <Stat
                  label="Écart de Brier (modèle − marché)"
                  value={formatSignedNumber(e.brier_difference, 4)}
                  valueClassName={e.sufficient_sample && e.brier_difference !== null ? signClass(-e.brier_difference) : ""}
                  hint={`IC 95 % ${formatInterval(e.brier_difference_ci95)} · négatif = modèle meilleur`}
                />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <CalibrationTable title="Calibration du modèle" bins={e.model_calibration} />
                <CalibrationTable title="Calibration du prix du marché" bins={e.market_calibration} />
              </div>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
