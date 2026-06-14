// components/simulation/HeatmapChart.tsx
'use client';

import { useRef, useEffect, useState } from 'react';
import type { ParameterCombo, StabilityZone } from '@/lib/simulation/types';

type Props = {
  combos: ParameterCombo[];
  stabilityZones: StabilityZone[];
  slStep: number;
};

interface TooltipData {
  x: number;
  y: number;
  combo: ParameterCombo;
}

export default function HeatmapChart({ combos, stabilityZones, slStep }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || combos.length === 0) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const W = 600;
    const H = 400;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    ctx.scale(dpr, dpr);

    // Gather unique SL and R:R values
    const slValues = [...new Set(combos.map((c) => c.slDistance))].sort((a, b) => a - b);
    const rrValues = [...new Set(combos.map((c) => c.rrRatio))].sort((a, b) => a - b);

    const cellW = W / slValues.length;
    const cellH = H / rrValues.length;

    const maxPnl = Math.max(...combos.map((c) => Math.abs(c.totalPnl)), 1);

    // Build lookup map
    const comboMap = new Map<string, ParameterCombo>();
    combos.forEach((c) => comboMap.set(`${c.slDistance}:${c.rrRatio}`, c));

    // Helper to get color
    const getColor = (pnl: number): string => {
      if (pnl === 0) return '#e2e8f0'; // slate-200
      const ratio = pnl / maxPnl; // -1 to 1
      if (ratio > 0) {
        // green: #22c55e to #14532d
        const intensity = Math.sqrt(ratio); // perceptual
        const r = Math.round(34 + (220 - 34) * (1 - intensity));
        const g = Math.round(197 + (240 - 197) * (1 - intensity));
        const b = Math.round(93 + (69 - 93) * (1 - intensity));
        return `rgb(${r},${g},${b})`;
      } else {
        // red: #ef4444 to #7f1d1d
        const intensity = Math.sqrt(-ratio);
        const r = Math.round(220 + (127 - 220) * (1 - intensity));
        const g = Math.round(52 + (20 - 52) * (1 - intensity));
        const b = Math.round(52 + (29 - 52) * (1 - intensity));
        return `rgb(${r},${g},${b})`;
      }
    };

    // Draw cells
    slValues.forEach((sl, si) => {
      rrValues.forEach((rr, ri) => {
        const combo = comboMap.get(`${sl}:${rr}`);
        if (!combo) return;

        ctx.fillStyle = getColor(combo.totalPnl);
        ctx.fillRect(si * cellW, ri * cellH, cellW - 1, cellH - 1);
      });
    });

    // Mark stability zones
    stabilityZones.forEach((zone) => {
      const startIdx = slValues.findIndex((v) => v >= zone.slStart);
      const endIdx = slValues.findIndex((v) => v > zone.slEnd);
      if (startIdx < 0) return;
      const end = endIdx < 0 ? slValues.length : endIdx;

      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = 2;
      ctx.strokeRect(
        startIdx * cellW + 1,
        0,
        (end - startIdx) * cellW - 2,
        H
      );
    });

    // X axis labels (SL)
    ctx.fillStyle = '#64748b';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    const labelEvery = Math.max(1, Math.floor(slValues.length / 10));
    slValues.forEach((sl, i) => {
      if (i % labelEvery === 0) {
        ctx.fillText(sl.toFixed(0), i * cellW + cellW / 2, H - 2);
      }
    });

    // Y axis labels (R:R)
    ctx.textAlign = 'right';
    const rrLabelEvery = Math.max(1, Math.floor(rrValues.length / 8));
    rrValues.forEach((rr, i) => {
      if (i % rrLabelEvery === 0) {
        ctx.fillText(rr.toFixed(1), 38, i * cellH + cellH / 2 + 3);
      }
    });

    // Axis titles
    ctx.fillStyle = '#475569';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('SL Distance (points)', W / 2, H - 2);

    ctx.save();
    ctx.translate(14, H / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('R:R Ratio', 0, 0);
    ctx.restore();
  }, [combos, stabilityZones, slStep]);

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || combos.length === 0) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const W = 600;
    const H = 400;

    const slValues = [...new Set(combos.map((c) => c.slDistance))].sort((a, b) => a - b);
    const rrValues = [...new Set(combos.map((c) => c.rrRatio))].sort((a, b) => a - b);
    const cellW = W / slValues.length;
    const cellH = H / rrValues.length;

    const mx = (e.clientX - rect.left) * (W / rect.width);
    const my = (e.clientY - rect.top) * (H / rect.height);

    const slIdx = Math.floor(mx / cellW);
    const rrIdx = Math.floor(my / cellH);
    if (slIdx < 0 || rrIdx < 0 || slIdx >= slValues.length || rrIdx >= rrValues.length) {
      setTooltip(null);
      return;
    }

    const sl = slValues[slIdx];
    const rr = rrValues[rrIdx];
    const combo = combos.find((c) => c.slDistance === sl && c.rrRatio === rr);
    if (!combo) {
      setTooltip(null);
      return;
    }

    setTooltip({
      x: e.clientX - (containerRef.current?.getBoundingClientRect().left ?? 0) + 10,
      y: e.clientY - (containerRef.current?.getBoundingClientRect().top ?? 0) + 10,
      combo,
    });
  };

  const handleMouseLeave = () => setTooltip(null);

  if (combos.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-slate-400" style={{ height: 400 }}>
        No data
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative overflow-hidden">
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        className="cursor-crosshair"
      />
      {tooltip && (
        <div
          className="pointer-events-none absolute z-10 rounded border border-slate-300 bg-white px-3 py-2 text-xs shadow-lg"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <p className="font-semibold text-slate-700">
            SL={tooltip.combo.slDistance.toFixed(1)} | R:R={tooltip.combo.rrRatio.toFixed(1)}
          </p>
          <p className="mt-1">
            P&L:{' '}
            <span
              className={
                tooltip.combo.totalPnl > 0
                  ? 'text-green-600'
                  : tooltip.combo.totalPnl < 0
                  ? 'text-red-600'
                  : 'text-slate-500'
              }
            >
              ${tooltip.combo.totalPnl.toFixed(2)}
            </span>
          </p>
          <p>
            Win Rate: {(tooltip.combo.winRate * 100).toFixed(1)}% | Trades: {tooltip.combo.tradeCount}
          </p>
          <p>
            SL: {(tooltip.combo.slTriggerRate * 100).toFixed(1)}% | TP:{' '}
            {(tooltip.combo.tpTriggerRate * 100).toFixed(1)}%
          </p>
        </div>
      )}
    </div>
  );
}