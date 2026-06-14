// lib/simulation/runner.ts — Parameter sweep, aggregation, and stability analysis

import type {
  HeatmapPoint,
  ParameterCombo,
  SimulationConfig,
  SimulationSummary,
  SimulationWarning,
  StabilityZone,
  TradeContext,
  TradeSimResult,
} from './types';

import { simulateTrade } from './engine';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EPSILON = 1e-9;

/** Minimum number of trades required before flagging LOW_SAMPLE */
const LOW_SAMPLE_THRESHOLD = 3;

// ---------------------------------------------------------------------------
// Helper: build a unique key for a parameter combo
// ---------------------------------------------------------------------------

function comboKey(slDistance: number, rrRatio: number): string {
  return `${slDistance.toFixed(4)}|${rrRatio.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// Helper: generate SL and R:R value arrays from config
// ---------------------------------------------------------------------------

function buildSlValues(config: SimulationConfig): number[] {
  const vals: number[] = [];
  for (let v = config.slMin; v <= config.slMax + EPSILON; v += config.slStep) {
    vals.push(Math.round(v * 10000) / 10000);
  }
  return vals;
}

function buildRrValues(config: SimulationConfig): number[] {
  const vals: number[] = [];
  for (let v = config.rrMin; v <= config.rrMax + EPSILON; v += config.rrStep) {
    vals.push(Math.round(v * 10000) / 10000);
  }
  return vals;
}

// ---------------------------------------------------------------------------
// Main sweep function
// ---------------------------------------------------------------------------

/**
 * Run the full parameter sweep across all SL × R:R combinations.
 *
 * @param trades - Array of trade contexts to simulate
 * @param config - Simulation configuration (SL range, R:R range, etc.)
 * @returns SimulationSummary
 */
export function runParameterSweep(
  trades: TradeContext[],
  config: SimulationConfig
): SimulationSummary {
  const startTime = Date.now();

  // ── 1. Pre-filter trades by direction ─────────────────────────────────
  const filteredTrades =
    config.direction === 'ALL'
      ? trades
      : trades.filter((t) => t.side === config.direction);

  // ── 2. Build parameter grids ───────────────────────────────────────────
  const slValues = buildSlValues(config);
  const rrValues = buildRrValues(config);
  const totalCombos = slValues.length * rrValues.length;

  // ── 2b. Early exit when no trades match the filter ───────────────────
  if (filteredTrades.length === 0) {
    return {
      trades: [],
      config,
      combos: [],
      stabilityZones: [],
      warnings: [],
      actualPnl: 0,
      executionTimeMs: Date.now() - startTime,
      totalCombos,
    };
  }

  // ── 3. Simulate every trade × every parameter combination ──────────────
  // Map from comboKey → array of TradeSimResult
  const resultsMap = new Map<string, TradeSimResult[]>();

  for (const trade of filteredTrades) {
    for (const slDist of slValues) {
      for (const rr of rrValues) {
        const result = simulateTrade(
          trade,
          slDist,
          rr,
          config.conservativeWhenBothHit,
          config.dollarPerPoint
        );
        const key = comboKey(slDist, rr);
        const existing = resultsMap.get(key);
        if (existing) {
          existing.push(result);
        } else {
          resultsMap.set(key, [result]);
        }
      }
    }
  }

  // ── 4. Aggregate results ───────────────────────────────────────────────
  const { combos, executionTimeMs } = aggregateResults(
    filteredTrades,
    config,
    resultsMap,
    startTime
  );

  // ── 5. Stability zone analysis ─────────────────────────────────────────
  const stabilityZones = findStabilityZones(combos);

  // ── 6. Generate warnings ───────────────────────────────────────────────
  const warnings = generateWarnings(combos, stabilityZones, filteredTrades.length);

  // ── 7. Compute actual P&L baseline ────────────────────────────────────
  const actualPnl = filteredTrades.reduce((sum, t) => sum + t.actualProfit, 0);

  return {
    trades: filteredTrades,
    config,
    combos,
    stabilityZones,
    warnings,
    actualPnl: Number(actualPnl.toFixed(4)),
    executionTimeMs,
    totalCombos,
  };
}

// ---------------------------------------------------------------------------
// Aggregate helper
// ---------------------------------------------------------------------------

function aggregateResults(
  trades: TradeContext[],
  config: SimulationConfig,
  resultsMap: Map<string, TradeSimResult[]>,
  startTime: number
): { combos: ParameterCombo[]; executionTimeMs: number } {
  const slValues = buildSlValues(config);
  const rrValues = buildRrValues(config);
  const combos: ParameterCombo[] = [];

  for (const slDist of slValues) {
    for (const rr of rrValues) {
      const key = comboKey(slDist, rr);
      const results = resultsMap.get(key) ?? [];

      let totalPnl = 0;
      let winCount = 0;
      let lossCount = 0;
      let slTriggerCount = 0;
      let tpTriggerCount = 0;
      let manualCloseCount = 0;

      for (const r of results) {
        totalPnl += r.simulatedProfit;
        if (r.simulatedProfit > 0) {
          winCount++;
        } else if (r.simulatedProfit < 0) {
          lossCount++;
        }
        if (r.slTriggered) slTriggerCount++;
        if (r.tpTriggered) tpTriggerCount++;
        if (r.manualClose) manualCloseCount++;
      }

      const tradeCount = results.length;
      const winRate = tradeCount > 0 ? winCount / tradeCount : 0;
      const slTriggerRate = tradeCount > 0 ? slTriggerCount / tradeCount : 0;
      const tpTriggerRate = tradeCount > 0 ? tpTriggerCount / tradeCount : 0;
      const manualCloseRate = tradeCount > 0 ? manualCloseCount / tradeCount : 0;

      combos.push({
        slDistance: slDist,
        rrRatio: rr,
        totalPnl: Number(totalPnl.toFixed(4)),
        winCount,
        lossCount,
        tradeCount,
        winRate: Number(winRate.toFixed(4)),
        slTriggerRate: Number(slTriggerRate.toFixed(4)),
        tpTriggerRate: Number(tpTriggerRate.toFixed(4)),
        manualCloseRate: Number(manualCloseRate.toFixed(4)),
        slTriggerCount,
        tpTriggerCount,
        manualCloseCount,
      });
    }
  }

  const executionTimeMs = Date.now() - startTime;
  return { combos, executionTimeMs };
}

// ---------------------------------------------------------------------------
// Stability zone analysis
// ---------------------------------------------------------------------------

/**
 * Identify SL ranges where P&L is most stable (low volatility across R:R).
 *
 * Algorithm:
 *   1. Group combos by SL distance
 *   2. For each SL, compute P&L mean and standard deviation across all R:R
 *   3. Sort SL values by volatility (ascending)
 *   4. Merge adjacent SL bands with similar low volatility
 *   5. Return top zones (up to 5) sorted by stability
 */
export function findStabilityZones(combos: ParameterCombo[]): StabilityZone[] {
  if (combos.length === 0) return [];

  // Group by SL
  const bySl = new Map<number, ParameterCombo[]>();
  for (const c of combos) {
    const arr = bySl.get(c.slDistance);
    if (arr) {
      arr.push(c);
    } else {
      bySl.set(c.slDistance, [c]);
    }
  }

  // Compute mean and stddev of totalPnl for each SL across R:R axis
  type SlStats = { sl: number; mean: number; stddev: number; avgTriggerRate: number };
  const slStats: SlStats[] = [];

  for (const entry of Array.from(bySl.entries())) {
    const sl = entry[0];
    const group = entry[1];
    const pnls = group.map((c) => c.totalPnl);
    const mean = pnls.reduce((a, b) => a + b, 0) / pnls.length;
    const variance =
      pnls.reduce((sum, p) => sum + Math.pow(p - mean, 2), 0) / pnls.length;
    const stddev = Math.sqrt(variance);
    const avgTriggerRate =
      group.reduce((sum, c) => sum + c.slTriggerRate, 0) / group.length;

    slStats.push({
      sl,
      mean: Number(mean.toFixed(4)),
      stddev: Number(stddev.toFixed(4)),
      avgTriggerRate: Number(avgTriggerRate.toFixed(4)),
    });
  }

  // Sort by volatility (ascending = most stable first)
  slStats.sort((a, b) => a.stddev - b.stddev);

  // Build stability zones: merge adjacent SL values that are close together
  const zones: StabilityZone[] = [];
  let zoneStart: number | null = null;
  let zoneEnd: number | null = null;
  let zoneStddevSum = 0;
  let zoneMeanSum = 0;
  let zoneRateSum = 0;
  let zoneCount = 0;

  for (const stat of slStats) {
    if (zoneStart === null) {
      zoneStart = stat.sl;
      zoneEnd = stat.sl;
      zoneStddevSum = stat.stddev;
      zoneMeanSum = stat.mean;
      zoneRateSum = stat.avgTriggerRate;
      zoneCount = 1;
    } else if (Math.abs(stat.sl - (zoneEnd ?? 0)) <= 1) {
      // Adjacent SL — extend current zone
      zoneEnd = stat.sl;
      zoneStddevSum += stat.stddev;
      zoneMeanSum += stat.mean;
      zoneRateSum += stat.avgTriggerRate;
      zoneCount++;
    } else {
      // Gap detected — finalise current zone and start new one
      zones.push({
        slStart: zoneStart,
        slEnd: zoneEnd ?? zoneStart,
        pnlVolatility: Number((zoneStddevSum / zoneCount).toFixed(4)),
        avgPnl: Number((zoneMeanSum / zoneCount).toFixed(4)),
        avgTriggerRate: Number((zoneRateSum / zoneCount).toFixed(4)),
      });
      zoneStart = stat.sl;
      zoneEnd = stat.sl;
      zoneStddevSum = stat.stddev;
      zoneMeanSum = stat.mean;
      zoneRateSum = stat.avgTriggerRate;
      zoneCount = 1;
    }
  }

  // Flush last zone
  if (zoneStart !== null && zoneEnd !== null) {
    zones.push({
      slStart: zoneStart,
      slEnd: zoneEnd,
      pnlVolatility: Number((zoneStddevSum / zoneCount).toFixed(4)),
      avgPnl: Number((zoneMeanSum / zoneCount).toFixed(4)),
      avgTriggerRate: Number((zoneRateSum / zoneCount).toFixed(4)),
    });
  }

  // Return up to 5 most stable zones
  return zones.slice(0, 5);
}

// ---------------------------------------------------------------------------
// Warning generation
// ---------------------------------------------------------------------------

/**
 * Generate overfitting / parameter-sensitivity warnings based on combo data.
 */
export function generateWarnings(
  combos: ParameterCombo[],
  stabilityZones: StabilityZone[],
  totalTradeCount: number
): SimulationWarning[] {
  const warnings: SimulationWarning[] = [];

  if (combos.length === 0) return warnings;

  // ── 1. HIGH_VOLATILITY: detect SL ranges where P&L swings sharply ──────
  // Group by SL and compute stddev across R:R
  const bySl = groupBySl(combos);
  for (const entry of Array.from(bySl.entries())) {
    const slStr = entry[0];
    const group = entry[1];
    const sl = Number(slStr);
    const pnls = group.map((c) => c.totalPnl);
    const mean = pnls.reduce((a, b) => a + b, 0) / pnls.length;
    const range = Math.max(...pnls) - Math.min(...pnls);

    // Flag if range exceeds 50% of |mean| or if range > $100
    if (range > 100 && mean !== 0 && range / Math.abs(mean) > 0.5) {
      warnings.push({
        type: 'HIGH_VOLATILITY',
        severity: range > 200 ? 'HIGH' : 'MEDIUM',
        message: `SL=${sl} shows high P&L volatility (range $${range.toFixed(2)}) across R:R values. This may indicate overfitting risk.`,
        slRange: [sl, sl],
      });
    }
  }

  // ── 2. PARAM_SENSITIVE: detect sharp P&L jumps between adjacent SL ─────
  const sortedSl = Array.from(bySl.keys())
    .map(Number)
    .sort((a, b) => a - b);

  for (let i = 1; i < sortedSl.length; i++) {
    const prevSl = sortedSl[i - 1];
    const currSl = sortedSl[i];
    const prevGroup = bySl.get(String(prevSl)) ?? [];
    const currGroup = bySl.get(String(currSl)) ?? [];

    const prevMean =
      prevGroup.reduce((s, c) => s + c.totalPnl, 0) / prevGroup.length;
    const currMean =
      currGroup.reduce((s, c) => s + c.totalPnl, 0) / currGroup.length;
    const jump = Math.abs(currMean - prevMean);

    if (jump > 50) {
      warnings.push({
        type: 'PARAM_SENSITIVE',
        severity: jump > 100 ? 'HIGH' : 'MEDIUM',
        message: `P&L changes by $${jump.toFixed(2)} between SL=${prevSl} and SL=${currSl}. Small parameter adjustments cause large P&L swings.`,
        slRange: [prevSl, currSl],
      });
    }
  }

  // ── 3. LOW_SAMPLE: flag combinations with very few winning/losing trades ─
  for (const c of combos) {
    const effectiveCount = c.winCount + c.lossCount;
    if (effectiveCount > 0 && effectiveCount < LOW_SAMPLE_THRESHOLD) {
      warnings.push({
        type: 'LOW_SAMPLE',
        severity: 'LOW',
        message: `Combination SL=${c.slDistance}, R:R=${c.rrRatio} has only ${effectiveCount} effective trades. Results may not be statistically meaningful.`,
        slRange: [c.slDistance, c.slDistance],
        rrRange: [c.rrRatio, c.rrRatio],
      });
    }
  }

  // ── 4. EDGE_CASE: flag extreme SL values ──────────────────────────────
  const minSl = sortedSl[0];
  const maxSl = sortedSl[sortedSl.length - 1];

  if (minSl !== undefined) {
    const minGroup = bySl.get(String(minSl)) ?? [];
    const minPnl = minGroup.reduce((s, c) => s + c.totalPnl, 0) / minGroup.length;
    if (minPnl < -50) {
      warnings.push({
        type: 'EDGE_CASE',
        severity: 'MEDIUM',
        message: `Very tight SL (${minSl}) results in significant losses. Verify these trades are not being stopped out prematurely.`,
        slRange: [minSl, minSl],
      });
    }
  }

  if (maxSl !== undefined) {
    const maxGroup = bySl.get(String(maxSl)) ?? [];
    const maxPnl = maxGroup.reduce((s, c) => s + c.totalPnl, 0) / maxGroup.length;
    if (maxPnl > 50) {
      warnings.push({
        type: 'EDGE_CASE',
        severity: 'LOW',
        message: `Very wide SL (${maxSl}) shows positive P&L. Ensure this is not just a result of fewer SL triggers and verify it fits your risk tolerance.`,
        slRange: [maxSl, maxSl],
      });
    }
  }

  return warnings;
}

// ---------------------------------------------------------------------------
// Heatmap converter
// ---------------------------------------------------------------------------

/**
 * Convert SimulationSummary into an array of HeatmapPoint for the frontend.
 */
export function toHeatmap(summary: SimulationSummary): HeatmapPoint[] {
  // Build a Set of SL values that fall inside stability zones
  const inStabilityZone = new Set<number>();
  for (const zone of summary.stabilityZones) {
    for (
      let sl = zone.slStart;
      sl <= zone.slEnd + EPSILON;
      sl += summary.config.slStep
    ) {
      const rounded = Math.round(sl * 10000) / 10000;
      inStabilityZone.add(rounded);
    }
  }

  return summary.combos.map((c) => ({
    slDistance: c.slDistance,
    rrRatio: c.rrRatio,
    totalPnl: c.totalPnl,
    winRate: c.winRate,
    slTriggerRate: c.slTriggerRate,
    tpTriggerRate: c.tpTriggerRate,
    isInStabilityZone: inStabilityZone.has(c.slDistance),
    isBreakeven: Math.abs(c.totalPnl) < 1,
  }));
}

// ---------------------------------------------------------------------------
// Internal helper
// ---------------------------------------------------------------------------

function groupBySl(
  combos: ParameterCombo[]
): Map<string, ParameterCombo[]> {
  const map = new Map<string, ParameterCombo[]>();
  for (const c of combos) {
    const key = c.slDistance.toFixed(4);
    const arr = map.get(key);
    if (arr) {
      arr.push(c);
    } else {
      map.set(key, [c]);
    }
  }
  return map;
}