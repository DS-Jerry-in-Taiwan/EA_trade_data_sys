#!/usr/bin/env npx tsx
// scripts/sl-optimization-analysis.ts — Phase 15.4: SL Parameter Final Optimization
// Run with: cd apps/trade-review-web && npx tsx scripts/sl-optimization-analysis.ts

import { getTradesWithOhlcRange } from '../lib/queries/simulation';
import { runParameterSweep } from '../lib/simulation/runner';
import type { SimulationConfig, ParameterCombo, StabilityZone } from '../lib/simulation/types';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const SYMBOL = 'XAUUSDm';
// Using all data from beginning of year 2026 to latest available (2026-06-12)
const FROM = '2026-01-01';
const TO = '2026-06-22';  // Today, but DB only has up to 2026-06-12

// OOS split: in-sample = Jan-May (first ~5 months), out-of-sample = June (most recent)
const IS_FROM = '2026-01-01';
const IS_TO = '2026-05-31';
const OOS_FROM = '2026-06-01';
const OOS_TO = '2026-06-22';  // Today

// Current SL parameters (from SimulationConfigPanel — SL=8pt)
const CURRENT_SL = 8;
const CURRENT_RR = 7; // approx R:R ~7:1

// Sweep configuration
const SWEEP_CONFIG: SimulationConfig = {
  slMin: 4,
  slMax: 30,
  slStep: 1,
  rrMin: 1,
  rrMax: 10,
  rrStep: 1,
  direction: 'ALL',
  dollarPerPoint: 1.0,
  conservativeWhenBothHit: true,
};

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function fmt(v: number, decimals = 2): string {
  return v.toFixed(decimals);
}

function printCombo(label: string, c: ParameterCombo): void {
  console.log(
    `  ${label}  SL=${fmt(c.slDistance, 1)}  R:R=${fmt(c.rrRatio, 1)}  ` +
    `P&L=${fmt(c.totalPnl)}  winRate=${fmt(c.winRate * 100, 1)}%  ` +
    `SL trig=${fmt(c.slTriggerRate * 100, 1)}%  TP trig=${fmt(c.tpTriggerRate * 100, 1)}%  ` +
    `manual=${fmt(c.manualCloseRate * 100, 1)}%  n=${c.tradeCount}`
  );
}

function bestComboByPnl(combos: ParameterCombo[]): ParameterCombo | null {
  if (combos.length === 0) return null;
  return combos.reduce((best, c) => (c.totalPnl > best.totalPnl ? c : best));
}

// Find combo closest to a target SL and R:R
function findCombo(combos: ParameterCombo[], sl: number, rr: number): ParameterCombo | null {
  let best: ParameterCombo | null = null;
  let bestDist = Infinity;
  for (const c of combos) {
    const d = Math.abs(c.slDistance - sl) + Math.abs(c.rrRatio - rr) * 0.1;
    if (d < bestDist) { bestDist = d; best = c; }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  console.log('\n=== PHASE 15.4: SL PARAMETER FINAL OPTIMIZATION ===\n');
  console.log(`Symbol: ${SYMBOL}`);
  console.log(`Date range: ${FROM} ~ ${TO}`);
  console.log(`In-sample: ${IS_FROM} ~ ${IS_TO}`);
  console.log(`Out-of-sample: ${OOS_FROM} ~ ${OOS_TO}`);

  // ── Phase 1: Data Loading & Baseline ─────────────────────────────────────
  console.log('\n── Phase 1: Data Loading & Baseline ──');

  const allTrades = await getTradesWithOhlcRange({ symbol: SYMBOL, from: FROM, to: TO });
  const isTrades = await getTradesWithOhlcRange({ symbol: SYMBOL, from: IS_FROM, to: IS_TO });
  const oosTrades = await getTradesWithOhlcRange({ symbol: SYMBOL, from: OOS_FROM, to: OOS_TO });

  const actualPnlSum = allTrades.reduce((s, t) => s + t.actualProfit, 0);
  const isActualPnl = isTrades.reduce((s, t) => s + t.actualProfit, 0);
  const oosActualPnl = oosTrades.reduce((s, t) => s + t.actualProfit, 0);

  const buyCount = allTrades.filter(t => t.side === 'BUY').length;
  const sellCount = allTrades.filter(t => t.side === 'SELL').length;

  console.log(`\n  Total trades: ${allTrades.length} (BUY=${buyCount}, SELL=${sellCount})`);
  console.log(`  In-sample trades: ${isTrades.length}`);
  console.log(`  Out-of-sample trades: ${oosTrades.length}`);
  console.log(`  actualPnl sum: ${fmt(actualPnlSum)}`);
  console.log(`  In-sample actualPnl: ${fmt(isActualPnl)}`);
  console.log(`  Out-of-sample actualPnl: ${fmt(oosActualPnl)}`);

  // Verify data integrity
  const hasMinPrice = allTrades.filter(t => t.minPrice !== undefined).length;
  console.log(`  Trades with minPrice/maxPrice: ${hasMinPrice}/${allTrades.length}`);

  // ── Phase 2: Full sweep on all data ─────────────────────────────────────
  console.log('\n── Phase 2: Full SL×R:R Sweep (4-30pt × 1-10) ──');

  const summary = runParameterSweep(allTrades, SWEEP_CONFIG);

  console.log(`\n  Combos generated: ${summary.combos.length}`);
  console.log(`  Execution time: ${summary.executionTimeMs}ms`);
  console.log(`  Baseline actualPnl: ${fmt(summary.actualPnl)}`);
  console.log(`  Stability zones found: ${summary.stabilityZones.length}`);
  console.log(`  Warnings: ${summary.warnings.length}`);

  // Print all stability zones
  if (summary.stabilityZones.length > 0) {
    console.log('\n  Stability Zones:');
    for (const z of summary.stabilityZones) {
      console.log(
        `    SL ${z.slStart}–${z.slEnd}pt  avgPnl=${fmt(z.avgPnl)}  ` +
        `volatility=${fmt(z.pnlVolatility)}  avgTriggerRate=${fmt(z.avgTriggerRate * 100, 1)}%`
      );
    }
  }

  // Print all warnings
  if (summary.warnings.length > 0) {
    console.log('\n  Warnings:');
    for (const w of summary.warnings) {
      console.log(`    [${w.severity}] ${w.type}: ${w.message}`);
    }
  }

  // ── Phase 3: Key combos analysis ────────────────────────────────────────
  console.log('\n── Phase 3: Key Combos Comparison ──');

  // Current SL combo
  const currentCombo = findCombo(summary.combos, CURRENT_SL, CURRENT_RR);
  if (currentCombo) {
    printCombo('CURRENT', currentCombo);
  } else {
    console.log('  CURRENT combo not found in sweep range');
  }

  // Best P&L combo (single-point — for reference only)
  const best = bestComboByPnl(summary.combos);
  if (best) {
    printCombo('BEST-PNL', best);
  }

  // Find best combo within each stability zone
  console.log('\n  Best combo within each stability zone:');
  for (const zone of summary.stabilityZones) {
    const zoneCombos = summary.combos.filter(
      c => c.slDistance >= zone.slStart && c.slDistance <= zone.slEnd
    );
    const zoneBest = bestComboByPnl(zoneCombos);
    if (zoneBest) {
      printCombo(`ZONE[${zone.slStart}-${zone.slEnd}]`, zoneBest);
    }
  }

  // Scan P&L across SL range at R:R=7
  console.log('\n  P&L vs SL at R:R=7 (key SL values):');
  const keySLs = [4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30];
  for (const sl of keySLs) {
    const c = findCombo(summary.combos, sl, 7);
    if (c) {
      const marker = sl === CURRENT_SL ? ' ←CURRENT' : '';
      console.log(
        `    SL=${sl.toString().padStart(2)}  P&L=${fmt(c.totalPnl).padStart(8)}  ` +
        `winRate=${fmt(c.winRate * 100, 1).padStart(5)}%  ` +
        `SL trig=${fmt(c.slTriggerRate * 100, 1).padStart(5)}%${marker}`
      );
    }
  }

  // ── Phase 4: OOS Validation ─────────────────────────────────────────────
  console.log('\n── Phase 4: Out-of-Sample Validation ──');

  // Run sweep on in-sample
  const isSummary = runParameterSweep(isTrades, SWEEP_CONFIG);
  console.log(`\n  In-sample: ${isTrades.length} trades, actualPnl=${fmt(isSummary.actualPnl)}`);
  console.log(`  In-sample combos: ${isSummary.combos.length}`);
  console.log(`  In-sample stability zones: ${isSummary.stabilityZones.length}`);
  if (isSummary.stabilityZones.length > 0) {
    for (const z of isSummary.stabilityZones) {
      console.log(
        `    SL ${z.slStart}–${z.slEnd}pt  avgPnl=${fmt(z.avgPnl)}  ` +
        `volatility=${fmt(z.pnlVolatility)}`
      );
    }
  }

  // OOS sweep using the SAME sweep config
  const oosSummary = runParameterSweep(oosTrades, SWEEP_CONFIG);
  console.log(`\n  Out-of-sample: ${oosTrades.length} trades, actualPnl=${fmt(oosActualPnl)}`);
  console.log(`  Out-of-sample combos: ${oosSummary.combos.length}`);

  // Compare current SL performance IS vs OOS
  const isCurrent = findCombo(isSummary.combos, CURRENT_SL, CURRENT_RR);
  const oosCurrent = findCombo(oosSummary.combos, CURRENT_SL, CURRENT_RR);
  console.log('\n  Current SL performance (IS vs OOS):');
  if (isCurrent && oosCurrent) {
    console.log(
      `    IS:  P&L=${fmt(isCurrent.totalPnl)}  winRate=${fmt(isCurrent.winRate * 100, 1)}%  ` +
      `SL=${fmt(isCurrent.slTriggerRate * 100, 1)}%  TP=${fmt(isCurrent.tpTriggerRate * 100, 1)}%`
    );
    console.log(
      `    OOS: P&L=${fmt(oosCurrent.totalPnl)}  winRate=${fmt(oosCurrent.winRate * 100, 1)}%  ` +
      `SL=${fmt(oosCurrent.slTriggerRate * 100, 1)}%  TP=${fmt(oosCurrent.tpTriggerRate * 100, 1)}%`
    );
    const pnlChange = oosCurrent.totalPnl - isCurrent.totalPnl;
    const pnlChangePct = isCurrent.totalPnl !== 0 ? (pnlChange / Math.abs(isCurrent.totalPnl)) * 100 : 0;
    console.log(`    OOS P&L change vs IS: ${fmt(pnlChange)} (${fmt(pnlChangePct, 1)}%)`);
  }

  // Compare best-in-zone performance IS vs OOS
  if (isSummary.stabilityZones.length > 0) {
    const isZone = isSummary.stabilityZones[0]; // most stable
    const isZoneBest = bestComboByPnl(
      isSummary.combos.filter(c => c.slDistance >= isZone.slStart && c.slDistance <= isZone.slEnd)
    );
    const oosZoneBest = bestComboByPnl(
      oosSummary.combos.filter(c => c.slDistance >= isZone.slStart && c.slDistance <= isZone.slEnd)
    );

    console.log(`\n  Stable zone [${isZone.slStart}-${isZone.slEnd}] SL performance IS vs OOS:`);
    if (isZoneBest && oosZoneBest) {
      console.log(
        `    IS:  P&L=${fmt(isZoneBest.totalPnl)}  winRate=${fmt(isZoneBest.winRate * 100, 1)}%  ` +
        `SL=${fmt(isZoneBest.slTriggerRate * 100, 1)}%  TP=${fmt(isZoneBest.tpTriggerRate * 100, 1)}%`
      );
      console.log(
        `    OOS: P&L=${fmt(oosZoneBest.totalPnl)}  winRate=${fmt(oosZoneBest.winRate * 100, 1)}%  ` +
        `SL=${fmt(oosZoneBest.slTriggerRate * 100, 1)}%  TP=${fmt(oosZoneBest.tpTriggerRate * 100, 1)}%`
      );
      const zonePnlChange = oosZoneBest.totalPnl - isZoneBest.totalPnl;
      const zonePnlPct = isZoneBest.totalPnl !== 0 ? (zonePnlChange / Math.abs(isZoneBest.totalPnl)) * 100 : 0;
      console.log(`    OOS P&L change vs IS: ${fmt(zonePnlChange)} (${fmt(zonePnlPct, 1)}%)`);
    }
  }

  // OOS direction distribution
  const oosBuy = oosTrades.filter(t => t.side === 'BUY').length;
  const oosSell = oosTrades.filter(t => t.side === 'SELL').length;
  console.log(`\n  OOS direction mix: BUY=${oosBuy}, SELL=${oosSell}`);

  // ── Phase 5: Recommendation ───────────────────────────────────────────────
  console.log('\n── Phase 5: Recommendation ──');

  let recommendation: string;
  let rationale: string[];
  let risk: string;
  let rollback: string;

  const currentPnl = currentCombo?.totalPnl ?? 0;

  if (summary.stabilityZones.length === 0) {
    recommendation = 'KEEP CURRENT SL';
    rationale = [
      'No stability zone identified in the sweep.',
      'Parameter sensitivity is too high to reliably choose an alternative.',
      'Current SL=8pt with R:R~7:1 remains the default.',
    ];
    risk = 'HIGH: No stable improvement found; changing SL blindly risks degradation.';
    rollback = 'If adjusted, set a hard stop: revert if P&L drops >20% over 10 trades.';
  } else {
    const topZone = summary.stabilityZones[0];
    const zoneBest = bestComboByPnl(
      summary.combos.filter(c => c.slDistance >= topZone.slStart && c.slDistance <= topZone.slEnd)
    )!;

    const topZoneOOS = oosSummary.combos.find(
      c => c.slDistance >= topZone.slStart && c.slDistance <= topZone.slEnd && c.rrRatio === zoneBest.rrRatio
    );

    const oosPnlChange = topZoneOOS ? topZoneOOS.totalPnl - zoneBest.totalPnl : null;
    const oosPct = oosPnlChange !== null && zoneBest.totalPnl !== 0
      ? (oosPnlChange / Math.abs(zoneBest.totalPnl)) * 100
      : null;

    if (oosPct !== null && Math.abs(oosPct) < 30 && zoneBest.totalPnl > currentPnl) {
      const midSL = (topZone.slStart + topZone.slEnd) / 2;
      recommendation = `ADJUST TO SL=${midSL.toFixed(1)}pt (stable zone [${topZone.slStart}-${topZone.slEnd}])`;
      rationale = [
        `Stable zone identified: SL ${topZone.slStart}-${topZone.slEnd}pt, avgPnl=${fmt(topZone.avgPnl)}, volatility=${fmt(topZone.pnlVolatility)}`,
        `Zone best combo: SL=${zoneBest.slDistance} R:R=${zoneBest.rrRatio} P&L=${fmt(zoneBest.totalPnl)}`,
        `OOS P&L change: ${fmt(oosPnlChange ?? 0)} (${fmt(oosPct ?? 0)}%) — within acceptable threshold (<30%)`,
        `Current SL=${CURRENT_SL} P&L=${fmt(currentPnl)} — zone improvement=${fmt(zoneBest.totalPnl - currentPnl)}`,
      ];
      risk = 'MEDIUM: Zone-based adjustment reduces single-point overfitting risk. Monitor for 20 trades.';
      rollback = `Revert to SL=${CURRENT_SL} if OOS P&L degrades >20% vs in-sample.`;
    } else if (zoneBest.totalPnl <= currentPnl) {
      recommendation = 'KEEP CURRENT SL';
      rationale = [
        `Stable zone found [${topZone.slStart}-${topZone.slEnd}] but best P&L=${fmt(zoneBest.totalPnl)} ≤ current P&L=${fmt(currentPnl)}`,
        'No evidence that adjusting SL would improve outcomes.',
      ];
      risk = 'LOW: Keeping current parameters.';
      rollback = 'No change required.';
    } else {
      recommendation = 'KEEP CURRENT SL — OOS VALIDATION FAILED';
      rationale = [
        `Stable zone P&L degrades >30% in OOS (change=${fmt(oosPct ?? 0)}%)`,
        'High risk of overfitting to in-sample data.',
      ];
      risk = 'HIGH: Overfitting detected. Do not adjust SL based on this data.';
      rollback = 'No change required. Current SL=8pt stands.';
    }
  }

  console.log(`\n  RECOMMENDATION: ${recommendation}`);
  console.log('\n  Rationale:');
  for (const r of rationale) {
    console.log(`    - ${r}`);
  }
  console.log(`\n  Risk: ${risk}`);
  console.log(`  Rollback: ${rollback}`);

  // ── Phase 6: Summary Table ───────────────────────────────────────────────
  console.log('\n── Summary Table ──');
  console.log(`| Metric                | In-Sample  | Out-of-Sample | Full Period |`);
  console.log(`|-----------------------|------------|---------------|-------------|`);
  console.log(`| Trades                | ${String(isTrades.length).padStart(10)} | ${String(oosTrades.length).padStart(13)} | ${String(allTrades.length).padStart(11)} |`);
  console.log(`| actualPnl             | ${fmt(isActualPnl).padStart(10)} | ${fmt(oosActualPnl).padStart(13)} | ${fmt(actualPnlSum).padStart(11)} |`);
  if (isCurrent && oosCurrent && currentCombo) {
    console.log(`| Current SL P&L       | ${fmt(isCurrent.totalPnl).padStart(10)} | ${fmt(oosCurrent.totalPnl).padStart(13)} | ${fmt(currentCombo.totalPnl).padStart(11)} |`);
  }
  if (best) {
    console.log(`| Best P&L (any combo)  | ${String('SL=' + best.slDistance + ' RR=' + best.rrRatio).padStart(10)} |      —        | ${fmt(best.totalPnl).padStart(11)} |`);
  }
  console.log(`| Stability Zones      | ${String(isSummary.stabilityZones.length).padStart(10)} | ${String(oosSummary.stabilityZones.length).padStart(13)} | ${String(summary.stabilityZones.length).padStart(11)} |`);
  console.log(`| Warnings              | ${String(isSummary.warnings.length).padStart(10)} | ${String(oosSummary.warnings.length).padStart(13)} | ${String(summary.warnings.length).padStart(11)} |`);

  console.log('\n=== END OF ANALYSIS ===\n');
}

main().catch(err => {
  console.error('Analysis failed:', err);
  process.exit(1);
});
