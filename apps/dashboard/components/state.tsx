"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";

function defaultIsEmpty(data: unknown): boolean {
  if (data === null || data === undefined) return true;
  if (Array.isArray(data)) return data.length === 0;
  if (typeof data === "object" && "items" in data && Array.isArray(data.items)) {
    return data.items.length === 0;
  }
  return false;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const status = error instanceof ApiError ? error.status : null;
  const message = error instanceof ApiError ? error.detail : String(error);
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
    >
      <div className="font-medium">
        Erreur de l&apos;API
        {status !== null && status > 0 ? (
          <span className="ml-2 font-mono tabular-nums">HTTP {status}</span>
        ) : status === 0 ? (
          <span className="ml-2">(injoignable)</span>
        ) : null}
      </div>
      <p className="text-muted-foreground break-words">{message}</p>
      <div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Réessayer
        </Button>
      </div>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

export function LoadingState({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-busy="true" aria-label="Chargement">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

/**
 * Renders the loading / error / empty states of a query, then `children(data)`.
 * Every page goes through this so the three states look and behave the same.
 */
export function QueryState<T>({
  query,
  empty,
  isEmpty = defaultIsEmpty,
  rows,
  children,
}: {
  query: UseQueryResult<T>;
  empty: ReactNode;
  isEmpty?: (data: T) => boolean;
  rows?: number;
  children: (data: NonNullable<T>) => ReactNode;
}) {
  if (query.isPending) return <LoadingState rows={rows} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const data = query.data;
  if (data === null || data === undefined || isEmpty(data)) return <EmptyState>{empty}</EmptyState>;
  return <>{children(data as NonNullable<T>)}</>;
}
