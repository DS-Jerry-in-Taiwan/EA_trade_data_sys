// scripts/test-csv-export.ts
// Standalone Node.js test runner for csvExport.ts
// Run with: npx tsx scripts/test-csv-export.ts

import {
  exportCandlesToCsv,
  exportTradesToCsv,
  buildCsvFilename,
} from '../lib/csvExport';

// ---------------------------------------------------------------------------
// Mocks — browser globals referenced by downloadCsv()
// ---------------------------------------------------------------------------
const savedLinks: { href: string; download: string }[] = [];
(global as Record<string, unknown>).document = {
  createElement: (tag: string) => {
    if (tag === 'a') {
      const mock: Record<string, unknown> = {};
      savedLinks.push(mock as { href: string; download: string });
      return mock;
    }
    return {};
  },
  body: { appendChild: () => {}, removeChild: () => {} },
};
(global as Record<string, unknown>).URL = {
  createObjectURL: () => 'blob:mock-url',
  revokeObjectURL: () => {},
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, label: string) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ✅ ${label}`);
    passed++;
  } else {
    console.error(`  ❌ ${label}`);
    console.error(`    Actual:   ${a}`);
    console.error(`    Expected: ${e}`);
    failed++;
  }
}

function assertIncludes(haystack: string, needle: string, label: string) {
  if (haystack.includes(needle)) {
    console.log(`  ✅ ${label}`);
    passed++;
  } else {
    console.error(`  ❌ ${label}`);
    console.error(`    String does not include: ${needle}`);
    console.error(`    Actual: ${haystack}`);
    failed++;
  }
}

function assertLineCount(csv: string, expected: number, label: string) {
  const lines = csv.split('\n').length;
  if (lines === expected) {
    console.log(`  ✅ ${label}`);
    passed++;
  } else {
    console.error(`  ❌ ${label} (got ${lines} lines, expected ${expected})`);
    failed++;
  }
}

// ---------------------------------------------------------------------------
// Test Cases
// ---------------------------------------------------------------------------
console.log('\n=== exportCandlesToCsv ===');

const sampleCandles = [
  { time: '2026-01-01T00:00:00Z', open: 100.5, high: 101.2, low: 99.8, close: 100.9, volume: 1234 },
  { time: '2026-01-01T01:00:00Z', open: 100.9, high: 102.0, low: 100.5, close: 101.5, volume: 1500 },
];
const candleCsv = exportCandlesToCsv(sampleCandles);
assertLineCount(candleCsv, 3, 'Two candles → 3 lines (header + 2)');
assertIncludes(candleCsv, 'time,open,high,low,close,volume', 'Header matches CandleDto fields');
assertIncludes(candleCsv, '2026-01-01T00:00:00Z', 'Entry time preserved');
assertIncludes(candleCsv, '100.5', 'Open price preserved');

// Empty array — only header
const emptyCandleCsv = exportCandlesToCsv([]);
assertLineCount(emptyCandleCsv, 1, 'Empty candles → only header');
assertEqual(emptyCandleCsv, 'time,open,high,low,close,volume', 'Empty CSV header only');

console.log('\n=== exportTradesToCsv ===');

const sampleTrades = [
  {
    positionId: 1,
    symbol: 'XAUUSDm',
    side: 'BUY' as const,
    entryTime: '2026-01-01T00:00:00Z',
    entryPrice: 100.5,
    exitTime: '2026-01-01T01:00:00Z',
    exitPrice: 101.0,
    volume: 0.5,
    profit: 25.0,
    comment: 'Long entry',
    status: 'CLOSED' as const,
  },
  {
    positionId: 2,
    symbol: 'XAUUSDm',
    side: 'SELL' as const,
    entryTime: null,
    entryPrice: null,
    exitTime: null,
    exitPrice: null,
    volume: 0.3,
    profit: -10.0,
    comment: '',
    status: 'OPEN' as const,
  },
];
const tradeCsv = exportTradesToCsv(sampleTrades);
assertLineCount(tradeCsv, 3, 'Two trades → 3 lines');
assertIncludes(tradeCsv, 'positionId,symbol,side,entryTime,entryPrice,exitTime,exitPrice,volume,profit,comment,status', 'Header matches TradeDto fields');
// Verify null fields become empty string (not "null")
assertIncludes(tradeCsv, ',,', 'Null entryTime + entryPrice → double comma');
assertIncludes(tradeCsv, '2026-01-01T00:00:00Z', 'Non-null entryTime preserved');
assertIncludes(tradeCsv, '-10', 'Negative profit preserved');

// Empty trades
const emptyTradeCsv = exportTradesToCsv([]);
assertLineCount(emptyTradeCsv, 1, 'Empty trades → only header');
assertEqual(emptyTradeCsv, 'positionId,symbol,side,entryTime,entryPrice,exitTime,exitPrice,volume,profit,comment,status', 'Empty trade CSV header only');

console.log('\n=== RFC 4180 Escape Cases ===');

// Comma inside field value
const commaCandles = [{ time: 'A,B', open: 1, high: 2, low: 3, close: 4, volume: 5 }];
const commaCsv = exportCandlesToCsv(commaCandles);
assertIncludes(commaCsv, '"A,B"', 'Comma → field wrapped in double-quotes');

// Double-quote inside field value
const quoteCandles = [{ time: 'He said "Hi"', open: 1, high: 2, low: 3, close: 4, volume: 5 }];
const quoteCsv = exportCandlesToCsv(quoteCandles);
assertIncludes(quoteCsv, '"He said ""Hi"""', 'Double-quote escaped as "" within quotes');

// Newline inside field value
const newlineCandles = [{ time: 'Line1\nLine2', open: 1, high: 2, low: 3, close: 4, volume: 5 }];
const newlineCsv = exportCandlesToCsv(newlineCandles);
assertIncludes(newlineCsv, '"Line1\nLine2"', 'Newline → field wrapped in double-quotes');

// Combined: comma + quote
const mixedCandles = [{ time: 'A, "B"', open: 1, high: 2, low: 3, close: 4, volume: 5 }];
const mixedCsv = exportCandlesToCsv(mixedCandles);
assertIncludes(mixedCsv, '"A, ""B"""', 'Comma + quote → proper RFC 4180 escape');

// Trades with comment containing comma/quote/newline
const trickyTrades = [
  {
    positionId: 99,
    symbol: 'XAUUSDm',
    side: 'BUY' as const,
    entryTime: 'normal',
    entryPrice: 100,
    exitTime: 'also, normal',
    exitPrice: 110,
    volume: 0.1,
    profit: 1.0,
    comment: 'Entry at "100"\nbreakout',
    status: 'CLOSED' as const,
  },
];
const trickyCsv = exportTradesToCsv(trickyTrades);
assertIncludes(trickyCsv, '"also, normal"', 'exitTime with comma escaped');
assertIncludes(trickyCsv, '"Entry at ""100""\nbreakout"', 'comment with quote + newline escaped');

console.log('\n=== buildCsvFilename ===');

const fn1 = buildCsvFilename('XAUUSDm', 'H1', '2026-01-01', '2026-01-07', 'ohlc');
assertEqual(fn1, 'XAUUSDm_H1_2026-01-01_to_2026-01-07_ohlc.csv', 'Full params → correct format');

const fn2 = buildCsvFilename('EURUSDm', 'M5', undefined, undefined, 'trades');
assertEqual(fn2, 'EURUSDm_M5_none_to_none_trades.csv', 'No date range → "none" placeholder');

const fn3 = buildCsvFilename('BTC', 'H1', '2026-03-01', undefined, 'ohlc');
assertEqual(fn3, 'BTC_H1_2026-03-01_to_none_ohlc.csv', 'Partial date range → one "none"');

console.log('\n=== Summary ===');
console.log(`Total: ${passed + failed} | ✅ ${passed} passed | ❌ ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}