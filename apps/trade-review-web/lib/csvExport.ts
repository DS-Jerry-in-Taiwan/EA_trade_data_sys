// lib/csvExport.ts — Pure functions for CSV generation (RFC 4180 compliant)
// No external dependencies; uses native Blob + URL.createObjectURL for download.

import type { CandleDto, TradeDto } from './types';

/**
 * Escape a single field value according to RFC 4180.
 * - Fields containing comma (,), double-quote ("), or newline (\n) are wrapped in double-quotes.
 * - Internal double-quotes are escaped as two consecutive double-quotes ("").
 */
function escapeField(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  const str = String(value);
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/** Convert an array of field values to a CSV row string (no trailing newline). */
function toRow(fields: unknown[]): string {
  return fields.map(escapeField).join(',');
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Serialize an array of CandleDto to CSV format.
 * Header: time,open,high,low,close,volume
 */
export function exportCandlesToCsv(candles: CandleDto[]): string {
  const header = 'time,open,high,low,close,volume';
  const rows = candles.map((c) =>
    toRow([c.time, c.open, c.high, c.low, c.close, c.volume])
  );
  return [header, ...rows].join('\n');
}

/**
 * Serialize an array of TradeDto to CSV format.
 * Header: positionId,symbol,side,entryTime,entryPrice,exitTime,exitPrice,volume,profit,comment,status
 */
export function exportTradesToCsv(trades: TradeDto[]): string {
  const header =
    'positionId,symbol,side,entryTime,entryPrice,exitTime,exitPrice,volume,profit,comment,status';
  const rows = trades.map((t) =>
    toRow([
      t.positionId,
      t.symbol,
      t.side,
      t.entryTime,
      t.entryPrice,
      t.exitTime,
      t.exitPrice,
      t.volume,
      t.profit,
      t.comment,
      t.status,
    ])
  );
  return [header, ...rows].join('\n');
}

/**
 * Trigger a browser file download from a CSV string.
 * Uses Blob + createObjectURL — pure client-side, no server needed.
 */
export function downloadCsv(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Build a descriptive CSV filename including symbol, timeframe, and date range.
 * Format: {symbol}_{timeframe}_{from}_to_{to}_{type}.csv
 * If from/to are undefined, they appear as "none".
 */
export function buildCsvFilename(
  symbol: string,
  timeframe: string,
  from: string | undefined,
  to: string | undefined,
  type: 'ohlc' | 'trades'
): string {
  const fromStr = from ?? 'none';
  const toStr = to ?? 'none';
  return `${symbol}_${timeframe}_${fromStr}_to_${toStr}_${type}.csv`;
}