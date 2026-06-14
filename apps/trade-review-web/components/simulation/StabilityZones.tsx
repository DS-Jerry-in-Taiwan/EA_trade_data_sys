// components/simulation/StabilityZones.tsx
'use client';

import type { StabilityZone } from '@/lib/simulation/types';

type Props = {
  zones: StabilityZone[];
};

export default function StabilityZones({ zones }: Props) {
  if (zones.length === 0) {
    return (
      <p className="text-sm text-slate-400">No stability zones identified.</p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {zones.map((zone, i) => (
        <div
          key={`${zone.slStart}-${zone.slEnd}-${i}`}
          className="rounded-lg border border-slate-200 p-4 shadow-sm bg-white"
        >
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                SL Range
              </p>
              <p className="mt-0.5 text-sm font-semibold text-slate-800">
                {zone.slStart.toFixed(1)} – {zone.slEnd.toFixed(1)}
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                Avg P&L
              </p>
              <p
                className={`mt-0.5 text-sm font-semibold ${
                  zone.avgPnl > 0
                    ? 'text-green-600'
                    : zone.avgPnl < 0
                    ? 'text-red-600'
                    : 'text-slate-700'
                }`}
              >
                ${zone.avgPnl.toFixed(2)}
              </p>
            </div>
          </div>
          <div className="mt-3 flex gap-4">
            <div>
              <p className="text-xs text-slate-500">Volatility</p>
              <p className="text-sm font-medium text-slate-700">{zone.pnlVolatility.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Avg Trigger</p>
              <p className="text-sm font-medium text-slate-700">
                {(zone.avgTriggerRate * 100).toFixed(1)}%
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}