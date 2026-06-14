// components/chart/DateRangePicker.tsx
'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import type { Symbol, Timeframe } from '@/lib/types';

type DateRangePickerProps = {
  symbol: Symbol;
  timeframe: Timeframe;
  currentFrom?: string;
  currentTo?: string;
};

export default function DateRangePicker({
  symbol,
  timeframe,
  currentFrom,
  currentTo,
}: DateRangePickerProps) {
  const router = useRouter();
  const [from, setFrom] = useState(currentFrom ?? '');
  const [to, setTo] = useState(currentTo ?? '');
  const [error, setError] = useState<string | null>(null);

  const navigate = (newFrom: string, newTo: string) => {
    const params = new URLSearchParams();
    params.set('symbol', symbol);
    params.set('timeframe', timeframe);
    if (newFrom) params.set('from', newFrom);
    if (newTo) params.set('to', newTo);
    router.push(`/review?${params.toString()}`);
  };

  const handleApply = () => {
    if (!from || !to) {
      setError('Please select both from and to dates');
      return;
    }
    if (from > to) {
      setError('From date must be before or equal to to date');
      return;
    }
    const daysDiff = Math.ceil(
      (new Date(to).getTime() - new Date(from).getTime()) / (1000 * 60 * 60 * 24)
    );
    if (daysDiff > 90) {
      setError('Date range cannot exceed 90 days');
      return;
    }
    setError(null);
    navigate(from, to);
  };

  const handleQuickRange = (days: number) => {
    const toDate = new Date();
    const fromDate = new Date();
    fromDate.setDate(fromDate.getDate() - days);
    const formatDate = (d: Date) => d.toISOString().split('T')[0];
    const newFrom = formatDate(fromDate);
    const newTo = formatDate(toDate);
    setFrom(newFrom);
    setTo(newTo);
    setError(null);
    navigate(newFrom, newTo);
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium text-slate-700">Date Range:</span>

      <div className="flex items-center gap-2">
        <label htmlFor="date-from" className="text-sm text-slate-600">
          From
        </label>
        <input
          id="date-from"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </div>

      <div className="flex items-center gap-2">
        <label htmlFor="date-to" className="text-sm text-slate-600">
          To
        </label>
        <input
          id="date-to"
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </div>

      <button
        onClick={handleApply}
        className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        Apply
      </button>

      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500">Quick:</span>
        <button
          onClick={() => handleQuickRange(7)}
          className="text-xs text-blue-600 hover:text-blue-800 underline"
        >
          7 days
        </button>
        <button
          onClick={() => handleQuickRange(30)}
          className="text-xs text-blue-600 hover:text-blue-800 underline"
        >
          30 days
        </button>
        <button
          onClick={() => handleQuickRange(90)}
          className="text-xs text-blue-600 hover:text-blue-800 underline"
        >
          90 days
        </button>
      </div>

      {error && (
        <span className="text-xs text-red-600">{error}</span>
      )}
    </div>
  );
}