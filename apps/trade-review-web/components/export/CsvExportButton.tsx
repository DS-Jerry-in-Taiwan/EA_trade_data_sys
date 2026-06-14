// components/export/CsvExportButton.tsx
'use client';

import { useState } from 'react';
import type { CandleDto, TradeDto } from '@/lib/types';
import {
  exportCandlesToCsv,
  exportTradesToCsv,
  downloadCsv,
  buildCsvFilename,
} from '@/lib/csvExport';

type CsvExportButtonProps = {
  candles: CandleDto[];
  trades: TradeDto[];
  symbol: string;
  timeframe: string;
  from?: string;
  to?: string;
};

export default function CsvExportButton({
  candles,
  trades,
  symbol,
  timeframe,
  from,
  to,
}: CsvExportButtonProps) {
  const [exportType, setExportType] = useState<'ohlc' | 'trades'>('ohlc');

  const handleExport = () => {
    if (exportType === 'ohlc') {
      if (candles.length === 0) {
        alert('No OHLC data to export.');
        return;
      }
      const csv = exportCandlesToCsv(candles);
      const filename = buildCsvFilename(symbol, timeframe, from, to, 'ohlc');
      downloadCsv(csv, filename);
    } else {
      if (trades.length === 0) {
        alert('No trade data to export.');
        return;
      }
      const csv = exportTradesToCsv(trades);
      const filename = buildCsvFilename(symbol, timeframe, from, to, 'trades');
      downloadCsv(csv, filename);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={handleExport}
        className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1"
      >
        Export CSV
      </button>
      <select
        value={exportType}
        onChange={(e) => setExportType(e.target.value as 'ohlc' | 'trades')}
        className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-700 shadow-sm hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1"
      >
        <option value="ohlc">OHLC Data</option>
        <option value="trades">Trades Data</option>
      </select>
    </div>
  );
}