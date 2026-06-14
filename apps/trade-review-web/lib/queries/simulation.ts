// lib/queries/simulation.ts — Query for simulation: trades + M5 OHLC price range

import { getPgPool } from '@/lib/db';
import type { TradeContext } from '@/lib/simulation/types';

// ---------------------------------------------------------------------------
// Constants (same as lib/queries.ts)
// ---------------------------------------------------------------------------

const DEFAULT_DAYS = 90;

// ---------------------------------------------------------------------------
// Helper: validate and normalise date range (copied from lib/queries.ts)
// ---------------------------------------------------------------------------

function normaliseDateRange(from?: string, to?: string): { from: Date; to: Date } | null {
  const now = new Date();
  const defaultFrom = new Date(now);
  defaultFrom.setDate(defaultFrom.getDate() - DEFAULT_DAYS);

  const fromDate = from ? new Date(from) : defaultFrom;
  const toDate = to ? new Date(to) : now;

  if (isNaN(fromDate.getTime()) || isNaN(toDate.getTime())) return null;
  if (fromDate > toDate) return null;

  const daySpan = (toDate.getTime() - fromDate.getTime()) / (1000 * 60 * 60 * 24);
  if (daySpan > DEFAULT_DAYS) return null;

  return { from: fromDate, to: toDate };
}

// ---------------------------------------------------------------------------
// getTradesWithOhlcRange — returns TradeContext[] with maxPrice/minPrice
// ---------------------------------------------------------------------------

export async function getTradesWithOhlcRange(params: {
  symbol: string;
  from?: string;
  to?: string;
}): Promise<TradeContext[]> {
  const range = normaliseDateRange(params.from, params.to);
  if (!range) return [];

  const client = await getPgPool().connect();
  try {
    // ── Query 1: trade summaries grouped by position_id ──────────────────
    const tradesResult = await client.query<{
      position_id: number;
      side_raw: string;
      entry_price: number | null;
      exit_price: number | null;
      entry_time: Date | null;
      exit_time: Date | null;
      profit: number;
    }>(
      `SELECT
         d.position_id,
         d.type                                              AS side_raw,
         MIN(d.deal_time) FILTER (WHERE d.entry = 'IN')    AS entry_time,
         MIN(d.price)    FILTER (WHERE d.entry = 'IN')      AS entry_price,
         MAX(d.deal_time) FILTER (WHERE d.entry = 'OUT')   AS exit_time,
         MAX(d.price)    FILTER (WHERE d.entry = 'OUT')     AS exit_price,
         SUM(d.profit)                                      AS profit
       FROM trade_deals d
       WHERE d.symbol = $1
         AND d.deal_time >= $2 AND d.deal_time <= $3
       GROUP BY d.position_id, d.type
       ORDER BY entry_time DESC`,
      [params.symbol, range.from, range.to]
    );

    // ── Query 2: M5 OHLC candles for the same date range ─────────────────
    const ohlcResult = await client.query<{
      datetime: Date;
      high: number;
      low: number;
    }>(
      `SELECT datetime, high, low
       FROM price_ohlc
       WHERE symbol = $1
         AND timeframe = 'M5'
         AND datetime >= $2 AND datetime <= $3
       ORDER BY datetime ASC`,
      [params.symbol, range.from, range.to]
    );

    const candles = ohlcResult.rows;

    // ── Combine: attach maxPrice / minPrice to each trade ────────────────
    const results: TradeContext[] = [];

    for (const row of tradesResult.rows) {
      const entryTime = row.entry_time;
      const exitTime = row.exit_time;
      const entryPrice = row.entry_price;
      const exitPrice = row.exit_price;

      // Skip trades without entry/exit times
      if (entryTime == null || exitTime == null || entryPrice == null || exitPrice == null) {
        continue;
      }

      // Filter candles that fall within [entryTime, exitTime]
      const filtered = candles.filter(
        (c) => c.datetime >= entryTime && c.datetime <= exitTime
      );

      let maxPrice: number | undefined;
      let minPrice: number | undefined;

      if (filtered.length > 0) {
        maxPrice = Math.max(...filtered.map((c) => Number(c.high)));
        minPrice = Math.min(...filtered.map((c) => Number(c.low)));
      }

      results.push({
        positionId: Number(row.position_id),
        side: row.side_raw === 'BUY' || row.side_raw === '0' ? 'BUY' : 'SELL',
        entryPrice: Number(entryPrice),
        exitPrice: Number(exitPrice),
        actualProfit: Number(row.profit),
        maxPrice,
        minPrice,
      });
    }

    return results;
  } finally {
    client.release();
  }
}