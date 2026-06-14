// components/simulation/SimulationResults.tsx
'use client';

import { useState } from 'react';
import type { ParameterCombo } from '@/lib/simulation/types';

type SortKey = keyof ParameterCombo;
type SortDir = 'asc' | 'desc';

type Props = {
  combos: ParameterCombo[];
};

export default function SimulationResults({ combos }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('totalPnl');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sorted = [...combos].sort((a, b) => {
    const av = a[sortKey] as number;
    const bv = b[sortKey] as number;
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  const thCls = (key: SortKey) =>
    `px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide cursor-pointer select-none ${
      sortKey === key ? 'text-blue-600' : 'text-slate-500'
    } hover:text-blue-800`;

  const tdCls = 'px-3 py-2.5 text-sm text-slate-700';

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50">
          <tr>
            <th className={thCls('slDistance')} onClick={() => handleSort('slDistance')}>
              SL Dist {sortKey === 'slDistance' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={thCls('rrRatio')} onClick={() => handleSort('rrRatio')}>
              R:R {sortKey === 'rrRatio' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={`${thCls('totalPnl')} text-right`} onClick={() => handleSort('totalPnl')}>
              Total P&L {sortKey === 'totalPnl' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={`${thCls('winRate')} text-right`} onClick={() => handleSort('winRate')}>
              Win Rate {sortKey === 'winRate' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={`${thCls('slTriggerRate')} text-right`} onClick={() => handleSort('slTriggerRate')}>
              SL Trigger {sortKey === 'slTriggerRate' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={`${thCls('tpTriggerRate')} text-right`} onClick={() => handleSort('tpTriggerRate')}>
              TP Trigger {sortKey === 'tpTriggerRate' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
            <th className={`${thCls('tradeCount')} text-right`} onClick={() => handleSort('tradeCount')}>
              Trades {sortKey === 'tradeCount' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sorted.map((combo, i) => (
            <tr key={`${combo.slDistance}-${combo.rrRatio}-${i}`} className="hover:bg-slate-50">
              <td className={tdCls}>{combo.slDistance.toFixed(1)}</td>
              <td className={tdCls}>{combo.rrRatio.toFixed(1)}</td>
              <td className={`${tdCls} text-right font-medium`}>
                <span
                  className={
                    combo.totalPnl > 0
                      ? 'text-green-600'
                      : combo.totalPnl < 0
                      ? 'text-red-600'
                      : 'text-slate-500'
                  }
                >
                  ${combo.totalPnl.toFixed(2)}
                </span>
              </td>
              <td className={`${tdCls} text-right`}>
                {(combo.winRate * 100).toFixed(1)}%
              </td>
              <td className={`${tdCls} text-right`}>
                {(combo.slTriggerRate * 100).toFixed(1)}%
              </td>
              <td className={`${tdCls} text-right`}>
                {(combo.tpTriggerRate * 100).toFixed(1)}%
              </td>
              <td className={`${tdCls} text-right`}>{combo.tradeCount}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}