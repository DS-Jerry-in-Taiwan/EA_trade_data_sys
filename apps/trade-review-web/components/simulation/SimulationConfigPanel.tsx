// components/simulation/SimulationConfigPanel.tsx
'use client';

import { useState } from 'react';
import type { SimulationSummary } from '@/lib/simulation/types';
import SimulationResults from './SimulationResults';
import HeatmapChart from './HeatmapChart';
import StabilityZones from './StabilityZones';
import WarningsPanel from './WarningsPanel';

type ConfigState = {
  symbol: 'XAUUSDm' | 'EURUSDm' | 'GBPUSDm' | 'BTC';
  slMin: number;
  slMax: number;
  slStep: number;
  rrMin: number;
  rrMax: number;
  rrStep: number;
  direction: 'ALL' | 'BUY' | 'SELL';
  dollarPerPoint: number;
  conservativeWhenBothHit: boolean;
  from: string;
  to: string;
};

const DEFAULT_CONFIG: ConfigState = {
  symbol: 'XAUUSDm',
  slMin: 4,
  slMax: 30,
  slStep: 1,
  rrMin: 1,
  rrMax: 10,
  rrStep: 1,
  direction: 'ALL',
  dollarPerPoint: 1.0,
  conservativeWhenBothHit: true,
  from: '',
  to: '',
};

const SYMBOLS = ['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC'] as const;

type Props = {
  initialSymbol?: string;
};

export default function SimulationConfigPanel({ initialSymbol = 'XAUUSDm' }: Props) {
  const [config, setConfig] = useState<ConfigState>({
    ...DEFAULT_CONFIG,
    symbol: SYMBOLS.includes(initialSymbol as typeof SYMBOLS[number])
      ? (initialSymbol as ConfigState['symbol'])
      : 'XAUUSDm',
  });
  const [summary, setSummary] = useState<SimulationSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleChange = (field: keyof ConfigState, value: string | number | boolean) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
  };

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setSummary(null);

    try {
      const body: Record<string, unknown> = {
        symbol: config.symbol,
        slMin: config.slMin,
        slMax: config.slMax,
        slStep: config.slStep,
        rrMin: config.rrMin,
        rrMax: config.rrMax,
        rrStep: config.rrStep,
        direction: config.direction,
        dollarPerPoint: config.dollarPerPoint,
        conservativeWhenBothHit: config.conservativeWhenBothHit,
      };
      if (config.from) body.from = config.from;
      if (config.to) body.to = config.to;

      const res = await fetch('/api/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data as { error?: string }).error ?? `HTTP ${res.status}`);
      }

      const data: SimulationSummary = await res.json();
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  const inputCls =
    'rounded border border-slate-300 px-2 py-1.5 text-sm w-24 focus:outline-none focus:ring-1 focus:ring-blue-500';
  const labelCls = 'text-sm text-slate-600';

  return (
    <div className="space-y-6">
      {/* Config form */}
      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-4 text-base font-semibold text-slate-800">Simulation Parameters</h2>

        <div className="flex flex-wrap items-end gap-4">
          {/* Symbol */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-symbol" className={labelCls}>
              Symbol
            </label>
            <select
              id="sim-symbol"
              value={config.symbol}
              onChange={(e) => handleChange('symbol', e.target.value)}
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          {/* SL range */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-sl-min" className={labelCls}>
              SL Min
            </label>
            <input
              id="sim-sl-min"
              type="number"
              min={0.1}
              step={0.5}
              value={config.slMin}
              onChange={(e) => handleChange('slMin', parseFloat(e.target.value) || 0)}
              className={inputCls}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-sl-max" className={labelCls}>
              SL Max
            </label>
            <input
              id="sim-sl-max"
              type="number"
              min={0.1}
              step={0.5}
              value={config.slMax}
              onChange={(e) => handleChange('slMax', parseFloat(e.target.value) || 0)}
              className={inputCls}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-sl-step" className={labelCls}>
              SL Step
            </label>
            <input
              id="sim-sl-step"
              type="number"
              min={0.1}
              step={0.5}
              value={config.slStep}
              onChange={(e) => handleChange('slStep', parseFloat(e.target.value) || 0.1)}
              className={inputCls}
            />
          </div>

          {/* R:R range */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-rr-min" className={labelCls}>
              R:R Min
            </label>
            <input
              id="sim-rr-min"
              type="number"
              min={0}
              step={0.5}
              value={config.rrMin}
              onChange={(e) => handleChange('rrMin', parseFloat(e.target.value) || 0)}
              className={inputCls}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-rr-max" className={labelCls}>
              R:R Max
            </label>
            <input
              id="sim-rr-max"
              type="number"
              min={0}
              step={0.5}
              value={config.rrMax}
              onChange={(e) => handleChange('rrMax', parseFloat(e.target.value) || 0)}
              className={inputCls}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-rr-step" className={labelCls}>
              R:R Step
            </label>
            <input
              id="sim-rr-step"
              type="number"
              min={0.1}
              step={0.5}
              value={config.rrStep}
              onChange={(e) => handleChange('rrStep', parseFloat(e.target.value) || 0.1)}
              className={inputCls}
            />
          </div>

          {/* Direction */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-direction" className={labelCls}>
              Direction
            </label>
            <select
              id="sim-direction"
              value={config.direction}
              onChange={(e) =>
                handleChange('direction', e.target.value as ConfigState['direction'])
              }
              className="rounded border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="ALL">ALL</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>

          {/* Dollar per point */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-dpp" className={labelCls}>
              $/Point
            </label>
            <input
              id="sim-dpp"
              type="number"
              min={0.01}
              step={0.1}
              value={config.dollarPerPoint}
              onChange={(e) =>
                handleChange('dollarPerPoint', parseFloat(e.target.value) || 1.0)
              }
              className={inputCls}
            />
          </div>

          {/* Conservative checkbox */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-conservative" className={labelCls}>
              Conservative SL
            </label>
            <input
              id="sim-conservative"
              type="checkbox"
              checked={config.conservativeWhenBothHit}
              onChange={(e) => handleChange('conservativeWhenBothHit', e.target.checked)}
              className="mt-1.5 h-4 w-4 rounded border-slate-300 text-blue-600"
            />
          </div>

          {/* Date range */}
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-from" className={labelCls}>
              From
            </label>
            <input
              id="sim-from"
              type="date"
              value={config.from}
              onChange={(e) => handleChange('from', e.target.value)}
              className={inputCls}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="sim-to" className={labelCls}>
              To
            </label>
            <input
              id="sim-to"
              type="date"
              value={config.to}
              onChange={(e) => handleChange('to', e.target.value)}
              className={inputCls}
            />
          </div>

          {/* Run button */}
          <button
            onClick={handleRun}
            disabled={loading}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Running...' : 'Run Simulation'}
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700 text-sm">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-12">
          <div className="flex flex-col items-center gap-3 text-slate-500">
            <svg
              className="h-8 w-8 animate-spin text-blue-600"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            <span className="text-sm font-medium">Running simulation...</span>
          </div>
        </div>
      )}

      {/* Results */}
      {summary && (
        <>
          {/* Baseline vs simulated comparison */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                Actual P&L
              </p>
              <p
                className={`mt-1 text-xl font-semibold ${
                  summary.actualPnl > 0
                    ? 'text-green-600'
                    : summary.actualPnl < 0
                    ? 'text-red-600'
                    : 'text-slate-700'
                }`}
              >
                ${summary.actualPnl.toFixed(2)}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                Trades Simulated
              </p>
              <p className="mt-1 text-xl font-semibold text-slate-700">
                {summary.trades.length}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                Combinations
              </p>
              <p className="mt-1 text-xl font-semibold text-slate-700">
                {summary.totalCombos}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                Exec Time
              </p>
              <p className="mt-1 text-xl font-semibold text-slate-700">
                {summary.executionTimeMs}ms
              </p>
            </div>
          </div>

          {/* No trades */}
          {summary.combos.length === 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500">
              No trades found for the selected criteria
            </div>
          )}

          {/* Heatmap */}
          {summary.combos.length > 0 && (
            <>
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
                  P&L Heatmap
                </h2>
                <HeatmapChart
                  combos={summary.combos}
                  stabilityZones={summary.stabilityZones}
                  slStep={config.slStep}
                />
              </div>

              {/* Stability Zones */}
              {summary.stabilityZones.length > 0 && (
                <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
                    Stability Zones
                  </h2>
                  <StabilityZones zones={summary.stabilityZones} />
                </div>
              )}

              {/* Warnings */}
              {summary.warnings.length > 0 && (
                <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
                    Warnings
                  </h2>
                  <WarningsPanel warnings={summary.warnings} />
                </div>
              )}

              {/* Results table */}
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
                  All Parameter Combinations ({summary.combos.length})
                </h2>
                <SimulationResults combos={summary.combos} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}