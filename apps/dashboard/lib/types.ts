// Mirror of src/ambre/api/schemas.py (Task 10). Nullable fields stay `| null`.
// Datetimes are ISO strings as serialised by the API.

export type Action = "YES" | "NO" | "NO_TRADE";
export type ExecutionStatus = "SIMULATED" | "REJECTED" | "NOT_EXECUTED";
export type MarketStatus = "open" | "resolved" | "all";

export interface Health {
  status: string;
  db_ok: boolean;
  version: string;
}

export interface RiskConfig {
  max_position_fraction: number;
  max_total_exposure_fraction: number;
  kelly_fraction: number;
  minimum_edge: number;
  critical_drawdown: number;
  min_stake: number;
  fee_rate: number;
}

export interface SurvivalConfig {
  defensive_drawdown: number;
  defensive_stake_multiplier: number;
  failure_value_fraction: number;
  max_data_age_seconds: number;
}

export interface SystemStatus {
  execution_mode: string;
  orders_enabled: boolean;
  halted: boolean;
  halt_reason: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
  data_age_seconds: number | null;
  last_cycle_at: string | null;
  decisions_total: number;
  active_strategy: string;
  risk: RiskConfig;
  survival: SurvivalConfig;
}

export interface MarketRow {
  id: string;
  question: string;
  url: string;
  close_at: string | null;
  is_resolved: boolean;
  resolution: string | null;
  probability: number | null;
  total_liquidity: number | null;
  unique_bettor_count: number | null;
  observed_at: string | null;
  ambre_estimate: number | null;
  ambre_uncertainty: number | null;
  ambre_ev_per_mana: number | null;
  last_action: string | null;
}

export interface MarketsPage {
  items: MarketRow[];
  total: number;
}

export interface Market {
  id: string;
  question: string;
  slug: string;
  url: string;
  creator_username: string;
  mechanism: string;
  created_at: string;
  close_at: string | null;
  is_resolved: boolean;
  resolution: string | null;
  resolution_probability: number | null;
  resolution_at: string | null;
  group_slugs: string[];
}

export interface Snapshot {
  id: number;
  probability: number;
  pool_yes: number;
  pool_no: number;
  total_liquidity: number | null;
  volume: number | null;
  volume_24h: number | null;
  unique_bettor_count: number | null;
  observed_at: string;
  event_at: string | null;
  source_id: string;
}

export interface ResearchSource {
  provider_id: string;
  title: string | null;
  url: string | null;
  published_at: string | null;
  linked_at: string;
  evidence_level: string;
  usable_for_prediction: boolean;
}

export interface Decision {
  decision_id: string;
  market_id: string;
  market_question: string | null;
  created_at: string;
  model_version: string;
  strategy: string;
  market_probability: number;
  estimated_probability: number;
  conservative_probability: number | null;
  uncertainty: number;
  expected_value: number | null;
  ev_per_mana: number | null;
  action: string;
  /** Proposed stake; only placed when execution_status === "SIMULATED". */
  stake: number;
  shares: number;
  avg_price: number | null;
  reasons: string[];
  invalidation: string[];
  source_ids: string[];
  market_url: string | null;
  /** Research linked to the market before this decision. It never feeds the estimate. */
  research_sources: ResearchSource[];
  survival_state: string;
  risk_reasons: string[];
  execution_status: string;
  /** Who took the decision: the algorithm (AMBRE), the owner (USER) or Claude (CLAUDE). */
  actor: string;
  outcome: string | null;
  payout: number | null;
  settled_at: string | null;
}

export interface DecisionsPage {
  items: Decision[];
  total: number;
}

export interface MarketDetail {
  market: Market;
  snapshots: Snapshot[];
  decisions: Decision[];
}

export interface Simulation {
  id: string;
  name: string;
  status: string;
  initial_capital: number;
  created_at: string;
}

export interface Position {
  market_id: string;
  question: string | null;
  side: string;
  shares: number;
  cost: number;
  opened_at: string;
  current_price: number | null;
  current_value: number | null;
}

export interface Portfolio {
  simulation: Simulation;
  cash: number;
  positions_value: number;
  total_value: number;
  exposure_known: boolean;
  total_value_is_lower_bound: boolean;
  peak_value: number;
  /** null when exposure is unknown (total_value is only a lower bound). */
  drawdown: number | null;
  survival_state: string | null;
  survival_reasons: string[];
  open_positions: Position[];
}

export interface PortfolioPoint {
  at: string;
  cash: number;
  positions_value: number;
  total_value: number;
  peak_value: number;
  drawdown: number;
  survival_state: string;
  survival_reasons: string[];
}

export interface Control {
  halted: boolean;
  reason: string | null;
  updated_at: string | null;
}

export interface CalibrationBin {
  lower: number;
  upper: number;
  n: number;
  mean_forecast: number;
  observed_rate: number;
}

export interface Evaluation {
  n: number;
  sufficient_sample: boolean;
  min_sample: number;
  model_brier: number | null;
  market_brier: number | null;
  model_log_loss: number | null;
  market_log_loss: number | null;
  /** model minus market; negative means the model scored better. */
  brier_difference: number | null;
  brier_difference_ci95: [number, number] | null;
  model_calibration: CalibrationBin[];
  market_calibration: CalibrationBin[];
}

export interface BetLine {
  market_id: string;
  question: string | null;
  side: string;
  stake: number;
  entry_price: number;
  payout: number;
  pnl: number;
  resolution: string | null;
  settled_at: string;
}

export interface BetGroup {
  label: string;
  bets: number;
  won: number;
  lost: number;
  win_rate: number | null;
  staked: number;
  realized_pnl: number | null;
}

export interface PnlPoint {
  index: number;
  at: string;
  pnl: number;
  cumulative_pnl: number;
}

export interface DayCount {
  day: string;
  bets: number;
  staked: number;
}

export interface Stats {
  decisions: { total: number; simulated: number; rejected: number; no_trade: number };
  bets: number;
  open: number;
  settled: number;
  won: number;
  lost: number;
  pushed: number;
  refunded: number;
  win_rate: number | null;
  win_rate_ci95: [number, number] | null;
  market_implied_win_rate: number | null;
  model_implied_win_rate: number | null;
  sufficient_sample: boolean;
  min_sample: number;
  total_staked: number;
  open_stake: number;
  average_stake: number | null;
  median_stake: number | null;
  largest_stake: number | null;
  smallest_stake: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  total_pnl: number | null;
  roi: number | null;
  average_win: number | null;
  average_loss: number | null;
  profit_factor: number | null;
  best_bet: BetLine | null;
  worst_bet: BetLine | null;
  longest_win_streak: number;
  longest_loss_streak: number;
  /** Positive for a run of wins, negative for a run of losses. */
  current_streak: number;
  by_side: BetGroup[];
  by_entry_price: BetGroup[];
  pnl_curve: PnlPoint[];
  bets_per_day: DayCount[];
  recent_settled: BetLine[];
}

export interface OrderRequest {
  market_id: string;
  side: "YES" | "NO";
  /** The actor's own P(YES), strictly between 0 and 1. */
  probability: number;
  stake: number;
  actor: "USER" | "CLAUDE";
  request_id: string;
  note: string | null;
}

export interface CycleReport {
  survival_state: string;
  evaluated: number;
  ineligible: number;
  no_trade: number;
  simulated: number;
  rejected: number;
  skipped_duplicate: number;
  skipped_by_survival: number;
  skipped_no_capacity: number;
  settled: { settled: number; refunded: number; unknown: string[] };
  model_version: string;
}
