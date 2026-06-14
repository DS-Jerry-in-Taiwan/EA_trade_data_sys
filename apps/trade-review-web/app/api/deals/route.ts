// app/api/deals/route.ts — GET /api/deals?symbol=XAUUSDm&from=&to=
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getDeals } from '@/lib/queries';
import { dbErrorResponse } from '@/lib/db';

const QuerySchema = z.object({
  symbol: z.enum(['XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC']).optional(),
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

    const { symbol, from, to } = parsed.data;
    const deals = await getDeals({ symbol, from, to });
    return NextResponse.json(deals);
  } catch (error) {
    return dbErrorResponse(error);
  }
}