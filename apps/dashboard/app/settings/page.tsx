"use client";

import { useState } from "react";

import { QueryState } from "@/components/state";
import { Alert } from "@/components/stat";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useHalt, useResume, useSystem } from "@/lib/api";
import { useNow } from "@/lib/use-now";
import { formatDateTime, formatNumber, formatProb, formatRelativeTime } from "@/lib/format";
import type { SystemStatus } from "@/lib/types";

// Mirrors HaltIn.reason in src/ambre/api/schemas.py (min_length=3, max_length=500).
const REASON_MIN = 3;
const REASON_MAX = 500;

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

function EmergencyStop({ system }: { system: SystemStatus }) {
  const halt = useHalt();
  const resume = useResume();
  const [reason, setReason] = useState("");
  const [armed, setArmed] = useState(false);

  // Each action clears the other's state first, so only the latest error is ever shown.
  const error = halt.error ?? resume.error;
  const trimmed = reason.trim();
  const reasonValid = trimmed.length >= REASON_MIN && trimmed.length <= REASON_MAX;

  if (system.halted) {
    return (
      <div className="flex flex-col gap-3">
        <Alert tone="critical" title="Système à l'arrêt (HALTED)">
          {system.halt_reason ?? "Aucun motif renseigné."}
        </Alert>
        <div>
          <Button
            variant="outline"
            disabled={resume.isPending}
            onClick={() => {
              halt.reset();
              resume.mutate();
            }}
          >
            Reprendre
          </Button>
        </div>
        {error ? <p role="alert" className="text-sm text-destructive">{error.message}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Input
        value={reason}
        onChange={(e) => {
          setReason(e.target.value);
          setArmed(false);
        }}
        placeholder={`Motif de l'arrêt (${REASON_MIN} à ${REASON_MAX} caractères)`}
        aria-label="Motif de l'arrêt d'urgence"
        maxLength={REASON_MAX}
        className="max-w-md"
      />
      <div className="flex items-center gap-2">
        {armed ? (
          <>
            <Button
              variant="destructive"
              disabled={halt.isPending || !reasonValid}
              onClick={() => {
                resume.reset();
                // Disarm only on success: after a failure the stop is not in effect, so the form stays armed.
                halt.mutate(trimmed, {
                  onSuccess: () => {
                    setArmed(false);
                    setReason("");
                  },
                });
              }}
            >
              Confirmer l&apos;arrêt d&apos;urgence
            </Button>
            <Button variant="ghost" onClick={() => setArmed(false)}>
              Annuler
            </Button>
          </>
        ) : (
          <Button variant="destructive" disabled={!reasonValid} onClick={() => setArmed(true)}>
            Arrêt d&apos;urgence
          </Button>
        )}
      </div>
      {error ? <p role="alert" className="text-sm text-destructive">{error.message}</p> : null}
    </div>
  );
}

export default function SettingsPage() {
  const now = useNow();
  const system = useSystem();

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Réglages</h1>
        <p className="text-sm text-muted-foreground">Lecture seule, sauf l&apos;arrêt d&apos;urgence.</p>
      </div>

      <QueryState query={system} empty="Statut indisponible.">
        {(s) => (
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Exécution</CardTitle>
              </CardHeader>
              <CardContent>
                <Row label="Mode d'exécution" value={s.execution_mode} />
                <Row label="Ordres Manifold" value={s.orders_enabled ? "activés" : "désactivés"} />
                <p className="pt-2 text-xs text-muted-foreground">modifiable uniquement via .env</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Synchronisation</CardTitle>
              </CardHeader>
              <CardContent>
                <Row
                  label="Dernière synchro"
                  value={`${formatDateTime(s.last_sync_at)} (${formatRelativeTime(s.last_sync_at, now)})`}
                />
                <Row label="Âge des données" value={s.data_age_seconds === null ? "—" : `${formatNumber(s.data_age_seconds, 0)} s`} />
                <Row label="Dernier cycle" value={formatDateTime(s.last_cycle_at)} />
                <Row label="Dernière erreur" value={s.last_sync_error ?? "aucune"} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Limites de risque</CardTitle>
              </CardHeader>
              <CardContent>
                <Row label="Mise max (part du portefeuille)" value={formatProb(s.risk.max_position_fraction)} />
                <Row label="Exposition totale max" value={formatProb(s.risk.max_total_exposure_fraction)} />
                <Row label="Fraction de Kelly" value={formatNumber(s.risk.kelly_fraction)} />
                <Row label="Edge minimal après impact" value={formatProb(s.risk.minimum_edge)} />
                <Row label="Drawdown critique" value={formatProb(s.risk.critical_drawdown)} />
                <Row label="Mise minimale" value={formatNumber(s.risk.min_stake)} />
                <Row label="Frais" value={formatProb(s.risk.fee_rate)} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Survie</CardTitle>
              </CardHeader>
              <CardContent>
                <Row label="Drawdown défensif" value={formatProb(s.survival.defensive_drawdown)} />
                <Row label="Multiplicateur de mise défensif" value={formatNumber(s.survival.defensive_stake_multiplier)} />
                <Row label="Seuil d'échec (part du capital)" value={formatProb(s.survival.failure_value_fraction)} />
                <Row label="Âge max des données" value={`${formatNumber(s.survival.max_data_age_seconds, 0)} s`} />
              </CardContent>
            </Card>

            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle>Arrêt d&apos;urgence</CardTitle>
              </CardHeader>
              <CardContent>
                <EmergencyStop system={s} />
              </CardContent>
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}
