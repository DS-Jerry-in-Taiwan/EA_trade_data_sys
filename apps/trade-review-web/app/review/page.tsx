// app/review/page.tsx — Phase 2 trade review page with chart and timeframe switching
import { getSummary, getOhlc, getTrades } from '@/lib/queries';
import SummaryCards from '@/components/summary/SummaryCards';
import TradeChart from '@/components/chart/TradeChart';
import SymbolSelector from '@/components/chart/SymbolSelector';
import DateRangePicker from '@/components/chart/DateRangePicker';
import CsvExportButton from '@/components/export/CsvExportButton';
import TradeTable from '@/components/trades/TradeTable';
import Link from 'next/link';
import type { Timeframe, TradeDto, CandleDto, Symbol } from '@/lib/types';

const DEFAULT_SYMBOL: Symbol = 'XAUUSDm';
const VALID_TIMEFRAMES: Timeframe[] = ['H1', 'M5'];
const VALID_SYMBOLS: Symbol[] = ['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC'];

const isValidSymbol = (s: string | undefined): s is Symbol =>
  VALID_SYMBOLS.includes(s as Symbol);

export const dynamic = 'force-dynamic';

type Props = {
  searchParams: Promise<{ timeframe?: string; symbol?: string; from?: string; to?: string }>;
};

export default async function ReviewPage({ searchParams }: Props) {
  const params = await searchParams;
  const timeframe: Timeframe = VALID_TIMEFRAMES.includes(params.timeframe as Timeframe)
    ? (params.timeframe as Timeframe)
    : 'H1';
  const symbol: Symbol = isValidSymbol(params.symbol) ? params.symbol : DEFAULT_SYMBOL;
  const { from, to } = params;

  let summary: Awaited<ReturnType<typeof getSummary>>;
  let candles: CandleDto[];
  let trades: TradeDto[];

  try {
    [summary, candles, trades] = await Promise.all([
      getSummary(),
      getOhlc({ symbol, timeframe, from, to }),
      getTrades({ symbol, from, to }),
    ]);
  } catch {
    summary = {
      balance: null,
      equity: null,
      totalProfit: 0,
      winRate: 0,
      profitFactor: null,
      closedTrades: 0,
    };
    candles = [];
    trades = [];
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="mx-auto max-w-7xl">
          <h1 className="text-2xl font-bold text-slate-800">Trade Review</h1>
          <p className="mt-0.5 text-sm text-slate-500">{symbol}</p>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        {/* Summary cards */}
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Account Summary
          </h2>
          <SummaryCards data={summary} />
        </section>

        {/* Chart area */}
        <section>
          <TradeChart candles={candles} trades={trades} symbol={symbol} timeframe={timeframe} />
        </section>

        {/* Symbol selector */}
        <section>
          <SymbolSelector currentSymbol={symbol} timeframe={timeframe} from={from} to={to} />
        </section>

        {/* Timeframe selector */}
        <section className="flex items-center gap-3">
          <span className="text-sm font-medium text-slate-700">Timeframe:</span>
          {(['H1', 'M5'] as Timeframe[]).map((tf) => (
            <Link
              key={tf}
              href={`/review?symbol=${symbol}&timeframe=${tf}${from ? `&from=${from}` : ''}${to ? `&to=${to}` : ''}`}
              className={`rounded border px-3 py-1.5 text-sm font-medium transition-colors ${
                tf === timeframe
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {tf}
            </Link>
          ))}
        </section>

        {/* Date range picker */}
        <section>
          <DateRangePicker symbol={symbol} timeframe={timeframe} currentFrom={from} currentTo={to} />
        </section>

        {/* CSV Export */}
        <section className="flex items-center gap-3">
          <CsvExportButton
            candles={candles}
            trades={trades}
            symbol={symbol}
            timeframe={timeframe}
            from={from}
            to={to}
          />
          <Link
            href={`/simulate?symbol=${symbol}`}
            className="rounded border border-blue-300 bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-600 hover:bg-blue-100"
          >
            Simulation
          </Link>
        </section>

        {/* Recent trades table */}
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Recent Trades ({trades.length})
          </h2>
          <TradeTable trades={trades} />
        </section>
      </main>
    </div>
  );
}