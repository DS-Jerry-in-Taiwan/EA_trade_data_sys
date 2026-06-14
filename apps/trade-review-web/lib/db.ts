// lib/db.ts — Server-only PostgreSQL connection pool
import { Pool } from 'pg';
import { NextResponse } from 'next/server';

// Lazy singleton: pool is only created on first use (not at module import time).
// This prevents build-time failures when DATABASE_URL is not set during `next build`.
const globalForPool = globalThis as unknown as {
  _pgPool: Pool | undefined;
};

function getPool(): Pool {
  if (!globalForPool._pgPool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error(
        'DATABASE_URL environment variable is not set. ' +
        'Copy .env.local.example to .env.local and fill in your PostgreSQL connection string.'
      );
    }
    globalForPool._pgPool = new Pool({
      connectionString,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });
    if (process.env.NODE_ENV !== 'production') {
      globalForPool._pgPool = globalForPool._pgPool;
    }
  }
  return globalForPool._pgPool;
}

/**
 * Expose the pool via a getter so it is created lazily.
 * All query functions should call `getPool()` instead of accessing a module-level pool.
 */
export function getPgPool(): Pool {
  return getPool();
}

/**
 * Helper to safely return a 500 JSON response from an API route handler.
 * DB errors must not leak raw stack traces to the browser.
 */
export function dbErrorResponse(error: unknown) {
  console.error('[DB Error]', error);
  return NextResponse.json(
    { error: 'internal server error' },
    { status: 500 }
  );
}