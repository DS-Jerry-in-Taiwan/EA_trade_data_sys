#!/usr/bin/env npx tsx
// scripts/ytd-analysis.ts — Year-to-Date Analysis (2026 Jan to Now)

import { getTradesWithOhlcRange } from '../lib/queries/simulation';
import { runParameterSweep } from '../lib/simulation/runner';

async function main() {
  console.log('\n=== 2026 Year-to-Date Analysis (All Available Data) ===\n');
  console.log('DB date range: 2026-03-16 ~ 2026-06-12 (88 days, within 90-day limit)');
  console.log('Note: getTradesWithOhlcRange has 90-day max limit');
  console.log('');

  // Get all available data (limited by 90-day constraint in the function)
  const allTrades = await getTradesWithOhlcRange({
    symbol: 'XAUUSDm',
    from: '2026-03-15',  // Just before earliest trade
    to: '2026-06-12',    // Latest trade
  });

  console.log('Total trades loaded:', allTrades.length);

  if (allTrades.length === 0) {
    console.log('No trades found.');
    return;
  }

  // Basic stats
  const buyCount = allTrades.filter((t) => t.side === 'BUY').length;
  const sellCount = allTrades.filter((t) => t.side === 'SELL').length;
  const actualPnl = allTrades.reduce((s, t) => s + t.actualProfit, 0);
  const withOhlc = allTrades.filter((t) => t.minPrice !== undefined).length;

  console.log('BUY trades:', buyCount);
  console.log('SELL trades:', sellCount);
  console.log('Actual P&L:', actualPnl.toFixed(2));
  console.log('Trades with OHLC:', withOhlc + '/' + allTrades.length);
  console.log('');

  // Run sweep
  const config = {
    slMin: 4,
    slMax: 30,
    slStep: 1,
    rrMin: 1,
    rrMax: 10,
    rrStep: 1,
    direction: 'ALL' as const,
    dollarPerPoint: 1.0,
    conservativeWhenBothHit: true,
  };

  const summary = runParameterSweep(allTrades, config);
  console.log('--- Full Sweep Results ---');
  console.log('Combos:', summary.combos.length);
  console.log('Execution time:', summary.executionTimeMs, 'ms');
  console.log('');

  // Best combo
  const best = summary.combos.reduce(
    (b, c) => (c.totalPnl > b.totalPnl ? c : b),
    summary.combos[0]
  );
  console.log('🏆 BEST P&L COMBO:');
  console.log('  SL:', best.slDistance, 'R:R:', best.rrRatio);
  console.log('  Total P&L:', best.totalPnl.toFixed(2));
  console.log('  Win rate:', (best.winRate * 100).toFixed(1) + '%');
  console.log('  SL trigger rate:', (best.slTriggerRate * 100).toFixed(1) + '%');
  console.log('  TP trigger rate:', (best.tpTriggerRate * 100).toFixed(1) + '%');
  console.log('  Trade count:', best.tradeCount);
  console.log('');

  // Current SL=8pt at R:R=7:1
  const current = summary.combos.find((c) => c.slDistance === 8 && c.rrRatio === 7);
  if (current) {
    console.log('📍 CURRENT SL=8pt, R:R=7:1:');
    console.log('  Total P&L:', current.totalPnl.toFixed(2));
    console.log('  Win rate:', (current.winRate * 100).toFixed(1) + '%');
    console.log('  SL trigger rate:', (current.slTriggerRate * 100).toFixed(1) + '%');
    console.log('  TP trigger rate:', (current.tpTriggerRate * 100).toFixed(1) + '%');
  }
  console.log('');

  // Stability zones
  console.log('📊 STABILITY ZONES:', summary.stabilityZones.length);
  summary.stabilityZones.forEach((z, i) => {
    console.log('  Zone ' + (i + 1) + ': SL ' + z.slStart + '-' + z.slEnd + 'pt');
    console.log('    Avg P&L:', z.avgPnl.toFixed(2));
    console.log('    Volatility:', z.pnlVolatility.toFixed(2));
    console.log('    Avg SL trigger rate:', (z.avgTriggerRate * 100).toFixed(1) + '%');
  });
  console.log('');

  // P&L at R:R=7 for various SL
  console.log('📈 P&L vs SL at R:R=7:');
  const slValues = [4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30];
  slValues.forEach((sl) => {
    const c = summary.combos.find((x) => x.slDistance === sl && x.rrRatio === 7);
    if (c) {
      const marker = sl === 8 ? ' ← CURRENT' : '';
      console.log(
        '  SL=' +
          sl.toString().padStart(2) +
          '  P&L=' +
          c.totalPnl.toFixed(2).padStart(8) +
          '  win=' +
          (c.winRate * 100).toFixed(1).padStart(5) +
          '%' +
          '  SLtrig=' +
          (c.slTriggerRate * 100).toFixed(1).padStart(5) +
          '%' +
          marker
      );
    }
  });

  console.log('\n=== END OF ANALYSIS ===\n');
}

main().catch((err) => {
  console.error('Analysis failed:', err);
  process.exit(1);
});
