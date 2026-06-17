// components/chart/SymbolSelector.tsx
import Link from 'next/link';
import type { Symbol, Timeframe } from '@/lib/types';

type SymbolSelectorProps = {
  currentSymbol: Symbol;
  timeframe: Timeframe;
  from?: string;
  to?: string;
};

const SYMBOLS: Symbol[] = ['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC'];

export default function SymbolSelector({ currentSymbol, timeframe, from, to }: SymbolSelectorProps) {
  // Build query params, preserving existing timeframe/from/to and adding symbol
  const buildHref = (symbol: Symbol) => {
    const params = new URLSearchParams();
    params.set('symbol', symbol);
    params.set('timeframe', timeframe);
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    return `/review?${params.toString()}`;
  };

  return (
    <div className="flex items-center gap-3">
      <span className="text-sm font-medium text-slate-700">Symbol:</span>
      <div className="flex flex-wrap gap-2">
        {SYMBOLS.map((sym) => (
          <Link
            key={sym}
            href={buildHref(sym)}
            className={`rounded border px-3 py-1.5 text-sm font-medium transition-colors ${
              sym === currentSymbol
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            {sym}
          </Link>
        ))}
      </div>
    </div>
  );
}
