// components/summary/SummaryCards.tsx
import type { SummaryDto } from '@/lib/types';

type SummaryCardsProps = {
  data: SummaryDto;
};

export default function SummaryCards({ data }: SummaryCardsProps) {
  const cards: { label: string; value: string | number | null; highlight?: boolean }[] = [
    { label: 'Balance', value: data.balance != null ? `$${data.balance.toFixed(2)}` : '—' },
    { label: 'Equity', value: data.equity != null ? `$${data.equity.toFixed(2)}` : '—' },
    { label: 'Total Profit', value: `$${data.totalProfit.toFixed(2)}`, highlight: data.totalProfit > 0 },
    { label: 'Win Rate', value: `${(data.winRate * 100).toFixed(2)}%` },
    { label: 'Profit Factor', value: data.profitFactor != null ? data.profitFactor.toFixed(3) : '—' },
    { label: 'Closed Trades', value: data.closedTrades },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      {cards.map(({ label, value, highlight }) => (
        <div
          key={label}
          className={`rounded-lg border p-4 shadow-sm ${
            highlight
              ? 'bg-green-50 border-green-200 text-green-800'
              : 'bg-white border-slate-200 text-slate-800'
          }`}
        >
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
          <p className="mt-1 text-xl font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}