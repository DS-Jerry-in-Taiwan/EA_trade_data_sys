// app/api/ohlc/route.ts — GET /api/ohlc?symbol=XAUUSDm&timeframe=H1&from=&to=
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getOhlc } from '@/lib/queries';
import { dbErrorResponse } from '@/lib/db';

const QuerySchema = z.object({
  symbol: z.enum(['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC']).default('XAUUSDm'),
  timeframe: z.enum(['H1', 'M5']),
  from: z.string().optional(),
  to: z.string().optional(),
});

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const raw = Object.fromEntries(searchParams.entries());
    const parsed = QuerySchema.safeParse(raw);

    if (!parsed.success) {
      return NextResponse.json(
        { error: 'invalid query parameters', details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const { symbol, timeframe, from, to } = parsed.data;
    const candles = await getOhlc({ symbol, timeframe, from, to });
    return NextResponse.json(candles);
  } catch (error) {
    return dbErrorResponse(error);
  }
}