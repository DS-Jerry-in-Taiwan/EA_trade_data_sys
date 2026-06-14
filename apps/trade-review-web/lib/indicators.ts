// lib/indicators.ts — Pure mathematical indicator calculations (no React/Chart dependencies)

export interface IndicatorPoint {
  time: number; // Unix timestamp (seconds)
  value: number;
}

/**
 * Calculate Simple Moving Average (SMA)
 * For i >= period-1: sma[i] = sum(close[i-period+1] ... close[i]) / period
 * Points before period-1 are not produced (insufficient data)
 */
export function calculateSMA(
  data: { time: number; close: number }[],
  period: number
): IndicatorPoint[] {
  const result: IndicatorPoint[] = [];

  for (let i = period - 1; i < data.length; i++) {
    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += data[i - j].close;
    }
    result.push({
      time: data[i].time,
      value: sum / period,
    });
  }

  return result;
}

/**
 * Calculate Relative Strength Index (RSI)
 * Uses Wilder's smoothing method (exponential moving average)
 * Period defaults to 14
 * Returns RSI values in range [0, 100]
 * First RSI uses simple average; subsequent use smoothed average
 */
export function calculateRSI(
  data: { time: number; close: number }[],
  period: number = 14
): IndicatorPoint[] {
  if (data.length < period + 1) {
    return [];
  }

  const result: IndicatorPoint[] = [];

  // Calculate gains and losses
  const gains: number[] = [];
  const losses: number[] = [];

  for (let i = 1; i < data.length; i++) {
    const change = data[i].close - data[i - 1].close;
    gains.push(Math.max(change, 0));
    losses.push(Math.max(-change, 0));
  }

  // First average: simple moving average
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 0; i < period; i++) {
    avgGain += gains[i];
    avgLoss += losses[i];
  }
  avgGain /= period;
  avgLoss /= period;

  // First RSI value
  let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  let rsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);
  result.push({
    time: data[period].time,
    value: rsi,
  });

  // Subsequent RSI values: smoothed moving average
  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period;

    rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);

    result.push({
      time: data[i + 1].time,
      value: rsi,
    });
  }

  return result;
}
