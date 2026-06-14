// lib/types.ts — DTO types for the trade review frontend

export type Timeframe = 'H1' | 'M5';

export type Symbol = 'XAUUSDm' | 'EURUSDm' | 'GBPUSDm' | 'BTC';

export type CandleDto = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type TradeDto = {
  positionId: number;
  symbol: string;
  side: 'BUY' | 'SELL';
  entryTime: string | null;
  entryPrice: number | null;
  exitTime: string | null;
  exitPrice: number | null;
  volume: number;
  profit: number;
  comment: string;
  status: 'OPEN' | 'CLOSED' | 'BALANCE' | 'UNKNOWN';
};

export type SummaryDto = {
  balance: number | null;
  equity: number | null;
  totalProfit: number;
  winRate: number;
  profitFactor: number | null;
  closedTrades: number;
};

export type DealDto = {
  dealId: number;
  symbol: string;
  type: string;
  entry: string;
  volume: number;
  price: number;
  profit: number;
  comment: string;
  positionId: number;
  dealTime: string;
};