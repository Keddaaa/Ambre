"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  Control,
  CycleReport,
  Decision,
  DecisionsPage,
  Evaluation,
  MarketDetail,
  MarketsPage,
  MarketStatus,
  OrderRequest,
  Portfolio,
  PortfolioPoint,
  SystemStatus,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_AMBRE_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export const REFRESH_MS = 30_000;

export class ApiError extends Error {
  /** HTTP status, or 0 when the API could not be reached at all. */
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(status === 0 ? detail : `HTTP ${status} — ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc, msg, ...}]
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : JSON.stringify(d)))
        .join("; ");
    }
    return JSON.stringify(detail);
  }
  return fallback;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, `API injoignable (${API_URL}). Lancez \`uv run uvicorn ambre.api.app:app --port 8000\`.`);
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detailOf(body, res.statusText || "erreur"));
  }
  return (await res.json()) as T;
}

/** True for a 404 that carries the API's own "not found" detail, not any 404 (proxy, wrong base URL). */
export function isNotFoundWith(e: unknown, detailPrefix: string): boolean {
  return e instanceof ApiError && e.status === 404 && e.detail.startsWith(detailPrefix);
}

/** Same as fetchJson but resolves to null on 404 (e.g. no simulation yet). */
async function fetchJsonOrNull<T>(path: string): Promise<T | null> {
  try {
    return await fetchJson<T>(path);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function query(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const keys = {
  system: ["system"] as const,
  portfolio: ["portfolio"] as const,
  portfolioHistory: ["portfolio", "history"] as const,
  markets: (p: MarketsParams) => ["markets", p] as const,
  market: (id: string) => ["market", id] as const,
  decisions: (p: DecisionsParams) => ["decisions", p] as const,
  evaluation: ["evaluation"] as const,
};

export function useSystem() {
  return useQuery({ queryKey: keys.system, queryFn: () => fetchJson<SystemStatus>("/system") });
}

/** `data === null` means the API answered 404: no simulation exists yet. */
export function usePortfolio() {
  return useQuery({
    queryKey: keys.portfolio,
    queryFn: () => fetchJsonOrNull<Portfolio>("/portfolio"),
  });
}

/** `data === null` means no simulation exists yet. */
export function usePortfolioHistory() {
  return useQuery({
    queryKey: keys.portfolioHistory,
    queryFn: () => fetchJsonOrNull<PortfolioPoint[]>("/portfolio/history"),
  });
}

export interface MarketsParams {
  status: MarketStatus;
  q?: string;
  limit: number;
  offset: number;
}

export function useMarkets(params: MarketsParams) {
  return useQuery({
    queryKey: keys.markets(params),
    queryFn: () => fetchJson<MarketsPage>(`/markets${query({ ...params })}`),
    placeholderData: keepPreviousData,
  });
}

export function useMarket(id: string) {
  return useQuery({
    queryKey: keys.market(id),
    // `data === null` means the API itself reported an unknown market id; any other 404 is an error.
    queryFn: async () => {
      try {
        return await fetchJson<MarketDetail>(`/markets/${encodeURIComponent(id)}`);
      } catch (e) {
        if (isNotFoundWith(e, "unknown market")) return null;
        throw e;
      }
    },
  });
}

export interface DecisionsParams {
  action?: string;
  limit: number;
  offset: number;
}

export function useDecisions(params: DecisionsParams) {
  return useQuery({
    queryKey: keys.decisions(params),
    queryFn: () => fetchJson<DecisionsPage>(`/decisions${query({ ...params })}`),
    placeholderData: keepPreviousData,
  });
}

function useControlMutation<TVars>(fn: (vars: TVars) => Promise<Control>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: keys.system }),
        qc.invalidateQueries({ queryKey: keys.portfolio }),
      ]);
    },
  });
}

export function useHalt() {
  return useControlMutation((reason: string) => postJson<Control>("/control/halt", { reason }));
}

export function useResume() {
  return useControlMutation(() => postJson<Control>("/control/resume", { confirm: true }));
}

/** `data === null` means the API reported that no simulation exists yet. */
export function useEvaluation() {
  return useQuery({
    queryKey: keys.evaluation,
    queryFn: async () => {
      try {
        return await fetchJson<Evaluation>("/evaluation");
      } catch (e) {
        if (isNotFoundWith(e, "no active simulation")) return null;
        throw e;
      }
    },
  });
}

/** Manual PAPER order. A refusal by the risk engine is a normal answer (REJECTED), not an error. */
export function usePlaceOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (order: OrderRequest) => postJson<Decision>("/orders", order),
    onSettled: async (_data, _err, order) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: keys.portfolio }),
        qc.invalidateQueries({ queryKey: ["decisions"] }),
        qc.invalidateQueries({ queryKey: keys.market(order.market_id) }),
        qc.invalidateQueries({ queryKey: keys.system }),
      ]);
    },
  });
}

/** One paper cycle on the data already stored; refreshes everything it can change. */
export function useRunCycle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<CycleReport>("/control/run-cycle", {}),
    onSettled: async () => {
      await Promise.all(
        [keys.system, keys.portfolio, ["decisions"], ["markets"], keys.evaluation].map((queryKey) =>
          qc.invalidateQueries({ queryKey }),
        ),
      );
    },
  });
}
