// components/chart/TradeChart.tsx
'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  type CandlestickData,
  type Time,
  type SeriesMarker,
  type LineData,
  LineStyle,
} from 'lightweight-charts';
import type { CandleDto, Timeframe, TradeDto } from '@/lib/types';
import { calculateSMA, calculateRSI } from '@/lib/indicators';

export type TradeChartProps = {
  candles: CandleDto[];
  trades: TradeDto[];
  symbol: string;
  timeframe: Timeframe;
};

export default function TradeChart({ candles, trades, symbol, timeframe }: TradeChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ma20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ma50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const container = chartContainerRef.current;

    // Create chart with dark theme to match trading charts
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#1a1a2e' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      width: container.clientWidth,
      height: 500,
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: '#374151',
      },
      // Add left price scale for RSI
      leftPriceScale: {
        borderColor: '#374151',
        visible: true,
      },
    });

    // Add candlestick series (main pane)
    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderUpColor: '#26a69a',
      borderDownColor: '#ef5350',
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    // Add SMA 20 line series (yellow)
    const ma20Series = chart.addLineSeries({
      color: '#fbbf24',
      lineWidth: 1,
      title: 'SMA 20',
    });

    // Add SMA 50 line series (purple)
    const ma50Series = chart.addLineSeries({
      color: '#a78bfa',
      lineWidth: 1,
      title: 'SMA 50',
    });

    // Add RSI line series (light blue, left price scale)
    const rsiSeries = chart.addLineSeries({
      color: '#38bdf8',
      lineWidth: 1,
      title: 'RSI',
      priceScaleId: 'left',
      lastValueVisible: true,
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.01,
      },
    });

    // Add RSI reference lines (70 = overbought, 30 = oversold)
    rsiSeries.createPriceLine({
      price: 70,
      color: '#ef4444',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: '70',
    });

    rsiSeries.createPriceLine({
      price: 30,
      color: '#22c55e',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: '30',
    });

    // Configure RSI price scale to show only 0-100 range at bottom
    rsiSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candlestickSeriesRef.current = candlestickSeries;
    ma20SeriesRef.current = ma20Series;
    ma50SeriesRef.current = ma50Series;
    rsiSeriesRef.current = rsiSeries;

    // ResizeObserver for responsive behavior
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        chart.applyOptions({ width });
      }
    });

    resizeObserver.observe(container);

    // Cleanup function
    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candlestickSeriesRef.current = null;
      ma20SeriesRef.current = null;
      ma50SeriesRef.current = null;
      rsiSeriesRef.current = null;
    };
  }, []);

  // Update candle data when it changes
  useEffect(() => {
    if (!candlestickSeriesRef.current || candles.length === 0) return;

    const candleData: CandlestickData<Time>[] = candles
      .filter(c =>
        Number.isFinite(c.open) &&
        Number.isFinite(c.high) &&
        Number.isFinite(c.low) &&
        Number.isFinite(c.close)
      )
      .map((candle) => ({
        time: Math.floor(new Date(candle.time).getTime() / 1000) as Time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }));

    candlestickSeriesRef.current.setData(candleData);

    // Fit content to visible range
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [candles]);

  // Update indicator data when candles change
  useEffect(() => {
    if (candles.length === 0) {
      // Clear all indicators when no data
      ma20SeriesRef.current?.setData([]);
      ma50SeriesRef.current?.setData([]);
      rsiSeriesRef.current?.setData([]);
      return;
    }

    // Prepare data in required format (time in seconds, sorted ascending)
    const chartData = candles
      .filter(c => Number.isFinite(c.close))
      .map((c) => ({
        time: Math.floor(new Date(c.time).getTime() / 1000),
        close: c.close,
      }))
      .sort((a, b) => a.time - b.time);

    // Calculate SMA 20
    const sma20Data: LineData<Time>[] = calculateSMA(chartData, 20).map((point) => ({
      time: point.time as Time,
      value: point.value,
    }));
    ma20SeriesRef.current?.setData(sma20Data);

    // Calculate SMA 50 (only if enough data)
    const sma50Data: LineData<Time>[] = chartData.length >= 50
      ? calculateSMA(chartData, 50).map((point) => ({
          time: point.time as Time,
          value: point.value,
        }))
      : [];
    ma50SeriesRef.current?.setData(sma50Data);

    // Calculate RSI (period 14)
    const rsiData: LineData<Time>[] = calculateRSI(chartData, 14).map((point) => ({
      time: point.time as Time,
      value: point.value,
    }));
    rsiSeriesRef.current?.setData(rsiData);
  }, [candles]);

  // Update trade markers when trades change
  useEffect(() => {
    if (!candlestickSeriesRef.current || trades.length === 0) return;

    const markers: SeriesMarker<Time>[] = [];

    trades.forEach((trade) => {
      // Entry marker
      if (trade.entryTime && trade.entryPrice) {
        const entryTime = Math.floor(new Date(trade.entryTime).getTime() / 1000) as Time;
        markers.push({
          time: entryTime,
          position: trade.side === 'BUY' ? 'belowBar' : 'aboveBar',
          color: trade.side === 'BUY' ? '#22c55e' : '#ef4444',
          shape: trade.side === 'BUY' ? 'arrowUp' : 'arrowDown',
          text: `${trade.side === 'BUY' ? 'Buy' : 'Sell'} #${trade.positionId}`,
          size: 1.5,
        });
      }

      // Exit marker
      if (trade.exitTime && trade.exitPrice) {
        const exitTime = Math.floor(new Date(trade.exitTime).getTime() / 1000) as Time;
        markers.push({
          time: exitTime,
          position: trade.side === 'BUY' ? 'aboveBar' : 'belowBar',
          color: trade.profit >= 0 ? '#22c55e' : '#ef4444',
          shape: 'square',
          text: `Close #${trade.positionId} ${trade.profit >= 0 ? '+' : ''}${trade.profit.toFixed(0)}`,
          size: 1.2,
        });
      }
    });

    // Sort markers by time
    markers.sort((a, b) => (a.time as number) - (b.time as number));

    candlestickSeriesRef.current.setMarkers(markers);
  }, [trades]);

  // Show message when no data available
  if (candles.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-800">
            {symbol} — {timeframe}
          </h2>
        </div>
        <div className="flex h-96 items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50">
          <p className="text-slate-500">No chart data available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">
          {symbol} — {timeframe}
        </h2>
        <div className="flex items-center gap-4 flex-wrap">
          <span className="text-xs text-slate-500">
            <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1"></span>
            Buy Entry
          </span>
          <span className="text-xs text-slate-500">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 mr-1"></span>
            Sell Entry
          </span>
          <span className="text-xs text-slate-500">
            <span className="inline-block w-2 h-2 bg-slate-500 mr-1"></span>
            Exit
          </span>
          <span className="text-xs text-slate-500 ml-2">
            <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 mr-1"></span>
            SMA 20
          </span>
          <span className="text-xs text-slate-500">
            <span className="inline-block w-2 h-2 rounded-full bg-purple-400 mr-1"></span>
            SMA 50
          </span>
          <span className="text-xs text-slate-500">
            <span className="inline-block w-2 h-2 rounded-full bg-sky-400 mr-1"></span>
            RSI
          </span>
          <span className="text-sm text-slate-500 ml-2">{candles.length} candles</span>
        </div>
      </div>
      <div ref={chartContainerRef} className="h-[500px] w-full rounded-lg overflow-hidden" />
    </div>
  );
}