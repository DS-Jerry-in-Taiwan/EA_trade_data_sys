// app/api/simulate/route.ts — POST /api/simulate
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { dbErrorResponse } from '@/lib/db';
import { runParameterSweep } from '@/lib/simulation/runner';
import type { SimulationConfig } from '@/lib/simulation/types';
import { getTradesWithOhlcRange } from '@/lib/queries/simulation';

const SimulateRequestSchema = z.object({
  symbol: z.enum(['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC']),
  slMin: z.number().positive().default(4),
  slMax: z.number().positive().default(30),
  slStep: z.number().positive().default(1),
  rrMin: z.number().nonnegative().default(1),
  rrMax: z.number().nonnegative().default(10),
  rrStep: z.number().positive().default(1),
  direction: z.enum(['ALL', 'BUY', 'SELL']).default('ALL'),
  dollarPerPoint: z.number().positive().default(1.0),
  conservativeWhenBothHit: z.boolean().default(true),
  from: z.string().optional(),
  to: z.string().optional(),
}).refine((data) => data.slMin <= data.slMax, {
  message: 'slMin must be <= slMax',
  path: ['slMin'],
}).refine((data) => data.rrMin <= data.rrMax, {
  message: 'rrMin must be <= rrMax',
  path: ['rrMin'],
});

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const parsed = SimulateRequestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: 'invalid request body', details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const data = parsed.data;

    const config: SimulationConfig = {
      slMin: data.slMin,
      slMax: data.slMax,
      slStep: data.slStep,
      rrMin: data.rrMin,
      rrMax: data.rrMax,
      rrStep: data.rrStep,
      direction: data.direction,
      dollarPerPoint: data.dollarPerPoint,
      conservativeWhenBothHit: data.conservativeWhenBothHit,
    };

    const trades = await getTradesWithOhlcRange({
      symbol: data.symbol,
      from: data.from,
      to: data.to,
    });

    const summary = runParameterSweep(trades, config);
    return NextResponse.json(summary);
  } catch (error) {
    return dbErrorResponse(error);
  }
}