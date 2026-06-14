// lib/simulation/types.ts — Type definitions for the trade simulation engine

/**
 * Simulation strategy configuration
 */
export interface SimulationConfig {
  slMin: number;
  slMax: number;
  slStep: number;
  rrMin: number;
  rrMax: number;
  rrStep: number;
  /** Filter trade direction: ALL | BUY | SELL */
  direction: 'ALL' | 'BUY' | 'SELL';
  /** Dollar value per point (e.g. 1.0 for standard gold contract) */
  dollarPerPoint: number;
  /** When both SL and TP are within OHLC range, prefer SL hit */
  conservativeWhenBothHit: boolean;
}

/**
 * Trade context — complete data for a single trade from the database.
 * Includes OHLC price range during the trade lifetime.
 */
export interface TradeContext {
  positionId: number;
  side: 'BUY' | 'SELL';
  entryPrice: number;
  exitPrice: number;
  actualProfit: number;
  /** Highest price during the trade (M5 OHLC high) */
  maxPrice?: number;
  /** Lowest price during the trade (M5 OHLC low) */
  minPrice?: number;
}

/** Exit reason classification */
export type ExitReason = 'TP_HIT' | 'SL_HIT' | 'MANUAL' | 'BOTH_HIT';

/**
 * Single-trade simulation result.
 * All three boolean flags are mutually exclusive in normal cases,
 * but BOTH_HIT sets both slTriggered and tpTriggered to true.
 */
export interface TradeSimResult {
  positionId: number;
  simulatedProfit: number;
  exitReason: ExitReason;
  /** Whether the stop-loss was triggered */
  slTriggered: boolean;
  /** Whether the take-profit was triggered */
  tpTriggered: boolean;
  /** Whether the trade was manually closed (not by SL/TP) */
  manualClose: boolean;
  /** SL price if applicable */
  slPrice?: number;
  /** TP price if applicable */
  tpPrice?: number;
}

/**
 * Aggregated result for a single SL distance × R:R ratio combination.
 */
export interface ParameterCombo {
  slDistance: number;
  rrRatio: number;
  totalPnl: number;
  winCount: number;
  lossCount: number;
  tradeCount: number;
  winRate: number;
  /** SL trigger rate (0–1) */
  slTriggerRate: number;
  /** TP trigger rate (0–1) */
  tpTriggerRate: number;
  /** Manual-close rate (0–1) */
  manualCloseRate: number;
  /** Number of trades where SL was triggered */
  slTriggerCount: number;
  /** Number of trades where TP was triggered */
  tpTriggerCount: number;
  /** Number of manually-closed trades */
  manualCloseCount: number;
}

/**
 * Stability zone — a range of SL values where P&L is relatively stable
 * (low volatility across R:R variations).
 */
export interface StabilityZone {
  /** SL range start (inclusive) */
  slStart: number;
  /** SL range end (inclusive) */
  slEnd: number;
  /** P&L standard deviation within this zone */
  pnlVolatility: number;
  /** Average P&L within this zone */
  avgPnl: number;
  /** Average trigger rate within this zone */
  avgTriggerRate: number;
}

/** Overfitting / parameter sensitivity warning type */
export type WarningType =
  | 'HIGH_VOLATILITY'   // P&L swings dramatically with small param changes
  | 'LOW_SAMPLE'        // Very few trades affected in this param range
  | 'PARAM_SENSITIVE'   // Parameter boundary is near an edge case
  | 'EDGE_CASE';        // Extreme SL values (very small or very large)

/**
 * Warning generated when a parameter region shows overfitting risk.
 */
export interface SimulationWarning {
  type: WarningType;
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  message: string;
  /** Related SL range [min, max] if applicable */
  slRange?: [number, number];
  /** Related R:R range [min, max] if applicable */
  rrRange?: [number, number];
}

/**
 * Complete simulation summary — the top-level output of the runner.
 * Does NOT contain any "optimal parameter" single conclusion.
 */
export interface SimulationSummary {
  /** Input trades used in this simulation */
  trades: TradeContext[];
  /** Original simulation configuration */
  config: SimulationConfig;
  /** All SL × R:R combination results */
  combos: ParameterCombo[];
  /** Identified stability zones */
  stabilityZones: StabilityZone[];
  /** Overfitting / risk warnings */
  warnings: SimulationWarning[];
  /** Sum of actualProfit from input trades (baseline for comparison) */
  actualPnl: number;
  /** Execution time in milliseconds */
  executionTimeMs: number;
  /** Total number of parameter combinations scanned */
  totalCombos: number;
}

/**
 * Data point for the frontend heatmap (2D grid cell).
 */
export interface HeatmapPoint {
  slDistance: number;
  rrRatio: number;
  totalPnl: number;
  winRate: number;
  slTriggerRate: number;
  tpTriggerRate: number;
  isInStabilityZone: boolean;
  isBreakeven: boolean;
}