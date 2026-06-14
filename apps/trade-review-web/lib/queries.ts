// lib/queries.ts — Server-side PostgreSQL queries for the trade review API

import { getPgPool } from './db';
import type { CandleDto, DealDto, SummaryDto, TradeDto, Timeframe } from './types';

// Server-side cap on candles returned per request
const MAX_CANDLES = 10000;

// How many days of data to fetch by default when no date range is supplied
const DEFAULT_DAYS = 90;

// Allowed symbols
const ALLOWED_SYMBOLS = new Set(['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC']);

// Allowed timeframes
const ALLOWED_TIMEFRAMES: Timeframe[] = ['H1', 'M5'];

// ---------------------------------------------------------------------------
// Helper: validate and normalise date range
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
// getSummary — returns account-level P&L summary
// ---------------------------------------------------------------------------

export async function getSummary(): Promise<SummaryDto> {
  const client = await getPgPool().connect();
  try {
    // Get latest account snapshot (balance, equity)
    const snapshotResult = await client.query<{
      balance: number;
      equity: number;
    }>(
      `SELECT balance, equity
       FROM trade_account_snapshots
       ORDER BY recorded_at DESC
       LIMIT 1`
    );

    const snapshot = snapshotResult.rows[0];

    // Get closed trade statistics from trade_deals
    // "closed" trades have entry = 'OUT' (outward direction = position was closed)
    const statsResult = await client.query<{
      total_profit: number;
      closed_trades: number;
      winning_trades: number;
      losing_trades: number;
    }>(
      `SELECT
         COALESCE(SUM(profit), 0)                      AS total_profit,
         COUNT(*)                                       AS closed_trades,
         COUNT(*) FILTER (WHERE profit > 0)            AS winning_trades,
         COUNT(*) FILTER (WHERE profit < 0)            AS losing_trades
       FROM trade_deals
       WHERE entry = 'OUT'`
    );

    const stats = statsResult.rows[0];

    const closedTrades = Number(stats.closed_trades) || 0;
    const winningTrades = Number(stats.winning_trades) || 0;
    const losingTrades = Number(stats.losing_trades) || 0;

    // Win rate: winning trades / total closed trades
    const winRate = closedTrades > 0 ? winningTrades / closedTrades : 0;

    // Profit factor: gross profit / gross loss (absolute values)
    const grossProfitResult = await client.query<{ sum: number }>(
      `SELECT COALESCE(SUM(profit), 0) AS sum
       FROM trade_deals
       WHERE entry = 'OUT' AND profit > 0`
    );
    const grossLossResult = await client.query<{ sum: number }>(
      `SELECT COALESCE(SUM(ABS(profit)), 0) AS sum
       FROM trade_deals
       WHERE entry = 'OUT' AND profit < 0`
    );
    const grossProfit = Number(grossProfitResult.rows[0].sum) || 0;
    const grossLoss = Number(grossLossResult.rows[0].sum) || 0;
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? grossProfit : null;

    return {
      balance: snapshot?.balance ?? null,
      equity: snapshot?.equity ?? null,
      totalProfit: Number(stats.total_profit) || 0,
      winRate: Math.round(winRate * 10000) / 10000, // 4 decimal places
      profitFactor,
      closedTrades,
    };
  } finally {
    client.release();
  }
}

// ---------------------------------------------------------------------------
// getOhlc — returns OHLC candles for a given symbol and timeframe
// ---------------------------------------------------------------------------

export async function getOhlc(params: {
  symbol: string;
  timeframe: Timeframe;
  from?: string;
  to?: string;
}): Promise<CandleDto[]> {
  if (!ALLOWED_SYMBOLS.has(params.symbol)) return [];
  if (!ALLOWED_TIMEFRAMES.includes(params.timeframe)) return [];

  const range = normaliseDateRange(params.from, params.to);
  if (!range) return [];

  const client = await getPgPool().connect();
  try {
    const result = await client.query<{
      datetime: Date;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
    }>(
      `SELECT datetime, open, high, low, close, volume
       FROM price_ohlc
       WHERE symbol = $1
         AND timeframe = $2
         AND datetime >= $3
         AND datetime <= $4
       ORDER BY datetime ASC
       LIMIT $5`,
      [params.symbol, params.timeframe, range.from, range.to, MAX_CANDLES]
    );

    return result.rows.map((row) => ({
      time: row.datetime.toISOString(),
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
      volume: Number(row.volume),
    }));
  } finally {
    client.release();
  }
}

// ---------------------------------------------------------------------------
// getDeals — returns raw deal records
// ---------------------------------------------------------------------------

export async function getDeals(params: {
  symbol?: string;
  from?: string;
  to?: string;
}): Promise<DealDto[]> {
  const range = normaliseDateRange(params.from, params.to);
  if (!range) return [];

  const client = await getPgPool().connect();
  try {
    let query = `
      SELECT deal_id, symbol, type, entry, volume, price, profit, comment, position_id, deal_time
      FROM trade_deals
      WHERE deal_time >= $1 AND deal_time <= $2
    `;
    const queryParams: (string | Date)[] = [range.from, range.to];
    let paramIndex = 3;

    if (params.symbol && ALLOWED_SYMBOLS.has(params.symbol)) {
      query += ` AND symbol = $${paramIndex}`;
      queryParams.push(params.symbol);
      paramIndex++;
    }

    query += ` ORDER BY deal_time DESC LIMIT ${MAX_CANDLES}`;

    const result = await client.query(query, queryParams);

    return result.rows.map((row) => ({
      dealId: Number(row.deal_id),
      symbol: String(row.symbol),
      type: String(row.type),
      entry: String(row.entry),
      volume: Number(row.volume),
      price: Number(row.price),
      profit: Number(row.profit),
      comment: String(row.comment ?? ''),
      positionId: Number(row.position_id),
      dealTime: (row.deal_time as Date).toISOString(),
    }));
  } finally {
    client.release();
  }
}

// ---------------------------------------------------------------------------
// getTrades — returns trade (position) summaries built from deal data
// ---------------------------------------------------------------------------

export async function getTrades(params: {
  symbol?: string;
  from?: string;
  to?: string;
}): Promise<TradeDto[]> {
  const range = normaliseDateRange(params.from, params.to);
  if (!range) return [];

  const client = await getPgPool().connect();
  try {
    // Build a CTE that aggregates deal data into position-level summaries.
    // For each position_id, we get the first entry deal (IN) and the last exit deal (OUT).
    let query = `
      WITH position_stats AS (
        SELECT
          d.position_id,
          d.symbol,
          d.type                                              AS side_raw,
          MIN(d.deal_time) FILTER (WHERE d.entry = 'IN')    AS entry_time,
          MIN(d.price)    FILTER (WHERE d.entry = 'IN')      AS entry_price,
          MAX(d.deal_time) FILTER (WHERE d.entry = 'OUT')   AS exit_time,
          MAX(d.price)    FILTER (WHERE d.entry = 'OUT')   AS exit_price,
          SUM(d.volume)                                      AS volume,
          SUM(d.profit)                                      AS profit,
          MAX(d.comment)                                     AS comment,
          MAX(d.entry)                                       AS last_entry
        FROM trade_deals d
        WHERE d.deal_time >= $1 AND d.deal_time <= $2
    `;
    const queryParams: (string | Date)[] = [range.from, range.to];
    let paramIndex = 3;

    if (params.symbol && ALLOWED_SYMBOLS.has(params.symbol)) {
      query += ` AND d.symbol = $${paramIndex}`;
      queryParams.push(params.symbol);
      paramIndex++;
    }

    query += `
        GROUP BY d.position_id, d.symbol, d.type
        ORDER BY entry_time DESC
        LIMIT ${MAX_CANDLES}
      )
      SELECT
        position_id,
        symbol,
        CASE WHEN side_raw IN ('BUY', '0') THEN 'BUY' ELSE 'SELL' END AS side,
        entry_time,
        entry_price,
        exit_time,
        exit_price,
        volume,
        profit,
        comment,
        CASE
          WHEN last_entry = 'IN'  THEN 'OPEN'
          WHEN last_entry = 'OUT' THEN 'CLOSED'
          WHEN last_entry = 'BALANCE' THEN 'BALANCE'
          ELSE 'UNKNOWN'
        END AS status
      FROM position_stats
    `;

    const result = await client.query(query, queryParams);

    return result.rows.map((row) => ({
      positionId: Number(row.position_id),
      symbol: String(row.symbol),
      side: row.side as 'BUY' | 'SELL',
      entryTime: row.entry_time ? (row.entry_time as Date).toISOString() : null,
      entryPrice: row.entry_price != null ? Number(row.entry_price) : null,
      exitTime: row.exit_time ? (row.exit_time as Date).toISOString() : null,
      exitPrice: row.exit_price != null ? Number(row.exit_price) : null,
      volume: Number(row.volume),
      profit: Number(row.profit),
      comment: String(row.comment ?? ''),
      status: row.status as 'OPEN' | 'CLOSED' | 'BALANCE' | 'UNKNOWN',
    }));
  } finally {
    client.release();
  }
}