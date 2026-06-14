// components/simulation/WarningsPanel.tsx
'use client';

import type { SimulationWarning } from '@/lib/simulation/types';

type Props = {
  warnings: SimulationWarning[];
};

const severityClasses: Record<SimulationWarning['severity'], string> = {
  HIGH: 'border-red-200 bg-red-50 text-red-800',
  MEDIUM: 'border-yellow-200 bg-yellow-50 text-yellow-800',
  LOW: 'border-blue-200 bg-blue-50 text-blue-800',
};

const severityBadge: Record<SimulationWarning['severity'], string> = {
  HIGH: 'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW: 'bg-blue-100 text-blue-700',
};

const typeLabel: Record<SimulationWarning['type'], string> = {
  HIGH_VOLATILITY: 'High Volatility',
  LOW_SAMPLE: 'Low Sample',
  PARAM_SENSITIVE: 'Param Sensitive',
  EDGE_CASE: 'Edge Case',
};

export default function WarningsPanel({ warnings }: Props) {
  if (warnings.length === 0) {
    return <p className="text-sm text-slate-400">No warnings.</p>;
  }

  return (
    <div className="space-y-2">
      {warnings.map((w, i) => (
        <div
          key={`${w.type}-${w.severity}-${i}`}
          className={`rounded-lg border p-3 text-sm ${severityClasses[w.severity]}`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex rounded px-1.5 py-0.5 text-xs font-semibold ${severityBadge[w.severity]}`}
            >
              {w.severity}
            </span>
            <span className="font-medium">{typeLabel[w.type]}</span>
            <span className="text-slate-600">{w.message}</span>
          </div>
          {(w.slRange || w.rrRange) && (
            <div className="mt-1 flex gap-3 text-xs opacity-80">
              {w.slRange && (
                <span>SL: {w.slRange[0]}–{w.slRange[1]}</span>
              )}
              {w.rrRange && (
                <span>R:R: {w.rrRange[0]}–{w.rrRange[1]}</span>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}