// components/trades/TradeTable.tsx
'use client';

import type { TradeDto } from '@/lib/types';

type TradeTableProps = {
  trades: TradeDto[];
};

export default function TradeTable({ trades }: TradeTableProps) {
  if (trades.length === 0) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-slate-500">
        No trades found
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 bg-white text-sm">
        <thead className="bg-slate-50">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Position ID</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Symbol</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Side</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Entry Time</th>
            <th className="px-4 py-3 text-right font-medium text-slate-700">Entry Price</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Exit Time</th>
            <th className="px-4 py-3 text-right font-medium text-slate-700">Exit Price</th>
            <th className="px-4 py-3 text-right font-medium text-slate-700">Volume</th>
            <th className="px-4 py-3 text-right font-medium text-slate-700">Profit</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Comment</th>
            <th className="px-4 py-3 text-left font-medium text-slate-700">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {trades.map((trade, index) => (
            <tr key={`${trade.positionId}-${index}`} className="hover:bg-slate-50">
              <td className="px-4 py-3 text-slate-700">{trade.positionId}</td>
              <td className="px-4 py-3 text-slate-700">{trade.symbol}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                    trade.side === 'BUY'
                      ? 'bg-green-100 text-green-700'
                      : 'bg-red-100 text-red-700'
                  }`}
                >
                  {trade.side}
                </span>
              </td>
              <td className="px-4 py-3 text-slate-600">{trade.entryTime ?? '—'}</td>
              <td className="px-4 py-3 text-right text-slate-700">
                {trade.entryPrice != null ? trade.entryPrice.toFixed(5) : '—'}
              </td>
              <td className="px-4 py-3 text-slate-600">{trade.exitTime ?? '—'}</td>
              <td className="px-4 py-3 text-right text-slate-700">
                {trade.exitPrice != null ? trade.exitPrice.toFixed(5) : '—'}
              </td>
              <td className="px-4 py-3 text-right text-slate-700">{trade.volume.toFixed(2)}</td>
              <td
                className={`px-4 py-3 text-right font-medium ${
                  trade.profit > 0
                    ? 'text-green-600'
                    : trade.profit < 0
                    ? 'text-red-600'
                    : 'text-slate-700'
                }`}
              >
                {trade.profit.toFixed(2)}
              </td>
              <td className="px-4 py-3 text-slate-600 truncate max-w-32">{trade.comment || '—'}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                    trade.status === 'CLOSED'
                      ? 'bg-slate-100 text-slate-700'
                      : trade.status === 'OPEN'
                      ? 'bg-blue-100 text-blue-700'
                      : 'bg-yellow-100 text-yellow-700'
                  }`}
                >
                  {trade.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}