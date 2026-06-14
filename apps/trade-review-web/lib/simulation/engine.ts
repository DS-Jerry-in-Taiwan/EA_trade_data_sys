// lib/simulation/engine.ts — Core simulation logic for a single trade

import type { ExitReason, TradeContext, TradeSimResult } from './types';

// ---------------------------------------------------------------------------
// Helper: safe float comparison with epsilon
// ---------------------------------------------------------------------------

const EPSILON = 1e-9;

/** Returns true if a === b within EPSILON */
function eq(a: number, b: number): boolean {
  return Math.abs(a - b) < EPSILON;
}

/** Returns true if a >= b within EPSILON */
function gte(a: number, b: number): boolean {
  return a > b - EPSILON;
}

/** Returns true if a <= b within EPSILON */
function lte(a: number, b: number): boolean {
  return a < b + EPSILON;
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Simulate a single trade under a given SL distance and R:R ratio.
 *
 * Algorithm:
 *   BUY:  slPrice = entryPrice - slDistance
 *         tpPrice = entryPrice + slDistance * rrRatio
 *         - TP_HIT  if maxPrice >= tpPrice
 *         - SL_HIT  if minPrice <= slPrice
 *         - BOTH    if both conditions are true
 *
 *   SELL: slPrice = entryPrice + slDistance
 *         tpPrice = entryPrice - slDistance * rrRatio
 *         - TP_HIT  if minPrice <= tpPrice
 *         - SL_HIT  if maxPrice >= slPrice
 *         - BOTH    if both conditions are true
 *
 * When both SL and TP are in range:
 *   - conservative=true  → SL triggered (loss), profit = -slDistance * dollarPerPoint
 *   - conservative=false → TP triggered (gain), profit = +tpDistance * dollarPerPoint
 *
 * @param trade        - Trade context with OHLC range
 * @param slDistance   - SL distance in points, must be > 0
 * @param rrRatio      - R:R ratio, must be >= 0
 * @param conservativeWhenBothHit - Prefer SL when both are hit (default true)
 * @param dollarPerPoint - Dollar value per point (default 1.0)
 * @throws {Error} if slDistance <= 0 or rrRatio < 0
 */
export function simulateTrade(
  trade: TradeContext,
  slDistance: number,
  rrRatio: number,
  conservativeWhenBothHit: boolean = true,
  dollarPerPoint: number = 1.0
): TradeSimResult {
  // ── 1. Input validation ────────────────────────────────────────────────
  if (slDistance <= 0) {
    throw new Error('slDistance must be > 0');
  }
  if (rrRatio < 0) {
    throw new Error('rrRatio must be >= 0');
  }

  // ── 2. Fallback when OHLC data is missing ──────────────────────────────
  const high = trade.maxPrice;
  const low = trade.minPrice;

  if (high === undefined || low === undefined) {
    return {
      positionId: trade.positionId,
      simulatedProfit: trade.actualProfit,
      exitReason: 'MANUAL',
      slTriggered: false,
      tpTriggered: false,
      manualClose: true,
    };
  }

  // ── 3. Compute SL and TP prices by direction ───────────────────────────
  const entry = trade.entryPrice;
  let slPrice: number;
  let tpPrice: number;
  let tpDistance: number;

  if (trade.side === 'BUY') {
    slPrice = entry - slDistance;
    tpDistance = slDistance * rrRatio;
    tpPrice = entry + tpDistance;
  } else {
    // SELL
    slPrice = entry + slDistance;
    tpDistance = slDistance * rrRatio;
    tpPrice = entry - tpDistance;
  }

  // ── 4. Determine trigger status ────────────────────────────────────────
  let slTriggered = false;
  let tpTriggered = false;
  let exitReason: ExitReason;
  let profit: number;

  if (trade.side === 'BUY') {
    const tpInRange = gte(high, tpPrice);
    const slInRange = lte(low, slPrice);

    if (tpInRange && slInRange) {
      // Both SL and TP are within the OHLC range — use conservative rule
      slTriggered = true;
      tpTriggered = true;
      exitReason = 'BOTH_HIT';
      if (conservativeWhenBothHit) {
        profit = -slDistance * dollarPerPoint;
      } else {
        profit = tpDistance * dollarPerPoint;
      }
    } else if (tpInRange) {
      tpTriggered = true;
      exitReason = 'TP_HIT';
      profit = tpDistance * dollarPerPoint;
    } else if (slInRange) {
      slTriggered = true;
      exitReason = 'SL_HIT';
      profit = -slDistance * dollarPerPoint;
    } else {
      exitReason = 'MANUAL';
      profit = trade.actualProfit;
    }
  } else {
    // SELL
    const tpInRange = lte(low, tpPrice);
    const slInRange = gte(high, slPrice);

    if (tpInRange && slInRange) {
      slTriggered = true;
      tpTriggered = true;
      exitReason = 'BOTH_HIT';
      if (conservativeWhenBothHit) {
        profit = -slDistance * dollarPerPoint;
      } else {
        profit = tpDistance * dollarPerPoint;
      }
    } else if (tpInRange) {
      tpTriggered = true;
      exitReason = 'TP_HIT';
      profit = tpDistance * dollarPerPoint;
    } else if (slInRange) {
      slTriggered = true;
      exitReason = 'SL_HIT';
      profit = -slDistance * dollarPerPoint;
    } else {
      exitReason = 'MANUAL';
      profit = trade.actualProfit;
    }
  }

  return {
    positionId: trade.positionId,
    simulatedProfit: Number(profit.toFixed(4)),
    exitReason,
    slTriggered,
    tpTriggered,
    manualClose: !slTriggered && !tpTriggered,
    slPrice: slTriggered || exitReason === 'BOTH_HIT' ? slPrice : undefined,
    tpPrice: tpTriggered || exitReason === 'BOTH_HIT' ? tpPrice : undefined,
  };
}