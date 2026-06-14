// app/api/summary/route.ts — GET /api/summary
import { NextResponse } from 'next/server';
import { getSummary } from '@/lib/queries';
import { dbErrorResponse } from '@/lib/db';

export async function GET() {
  try {
    const summary = await getSummary();
    return NextResponse.json(summary);
  } catch (error) {
    return dbErrorResponse(error);
  }
}