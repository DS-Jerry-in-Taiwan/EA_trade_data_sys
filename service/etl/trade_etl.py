#!/usr/bin/env python3
"""
Trade Analytics Dashboard — ETL Script (Phase 1)

Fetches trade data from the MT5 Bridge API and loads it into PostgreSQL.
Also imports OHLC history CSV files into the price_ohlc table.

Usage:
    python3 /app/service/etl/trade_etl.py

Environment variables:
    TRADE_PG_HOST      (default: 172.17.0.1)
    TRADE_PG_PORT      (default: 5432)
    TRADE_PG_DB        (default: trade_analytics)
    TRADE_PG_USER      (default: postgres)
    TRADE_PG_PASSWORD  (default: postgres)
    READONLY_API_KEY   (required)
"""

import csv
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PG_CONFIG = {
    "host": os.environ.get("TRADE_PG_HOST", "172.17.0.1"),
    "port": int(os.environ.get("TRADE_PG_PORT", "5432")),
    "dbname": os.environ.get("TRADE_PG_DB", "trade_analytics"),
    "user": os.environ.get("TRADE_PG_USER", "postgres"),
    "password": os.environ.get("TRADE_PG_PASSWORD", "postgres"),
}

API_KEY = os.environ.get("READONLY_API_KEY")
API_BASE = "http://localhost:8090/api/v1"
HISTORY_DIR = "/app/service/data/history"

if not API_KEY:
    raise RuntimeError("READONLY_API_KEY environment variable is required")

# ---------------------------------------------------------------------------
# API helper
# ---------------------------------------------------------------------------


def fetch_api(endpoint: str, params: dict | None = None) -> dict | list:
    """Call the MT5 Bridge API and return the JSON response.

    Retries once on failure (network / non-2xx).
    """
    url = f"{API_BASE}/{endpoint}"
    headers = {"X-API-Key": API_KEY}
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            print(f"[WARN] fetch_api({endpoint}) attempt {attempt + 1} failed: {exc}")
            if attempt == 0:
                time.sleep(2)
            else:
                print(f"[ERROR] fetch_api({endpoint}) exhausted retries, returning empty")
                return [] if endpoint.startswith("history") else {}
    return {}  # should not be reached


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def upsert_deals(cursor, deals: list, login: int):
    """Insert or update trade_deals rows.

    API field -> DB column mapping:
        deal       -> deal_id
        order      -> order_id
        time       -> deal_time
    """
    sql = """
        INSERT INTO trade_deals
            (login, deal_id, symbol, type, entry, volume, price,
             profit, swap, commission, comment, position_id, order_id, deal_time)
        VALUES %s
        ON CONFLICT (deal_id) DO UPDATE SET
            symbol      = EXCLUDED.symbol,
            type        = EXCLUDED.type,
            entry       = EXCLUDED.entry,
            volume      = EXCLUDED.volume,
            price       = EXCLUDED.price,
            profit      = EXCLUDED.profit,
            swap        = EXCLUDED.swap,
            commission  = EXCLUDED.commission,
            comment     = EXCLUDED.comment,
            position_id = EXCLUDED.position_id,
            order_id    = EXCLUDED.order_id,
            deal_time   = EXCLUDED.deal_time
    """
    rows = []
    for d in deals:
        rows.append((
            login,
            int(d["deal"]),
            d.get("symbol", ""),
            d.get("type", ""),
            d.get("entry", ""),
            float(d.get("volume", 0)),
            float(d.get("price", 0)),
            float(d.get("profit", 0)),
            float(d.get("swap", 0)),
            float(d.get("commission", 0)),
            d.get("comment", "") or "",
            int(d.get("position_id", 0)),
            int(d.get("order", 0)),
            _parse_timestamp(d.get("time", "")),
        ))
    if rows:
        psycopg2.extras.execute_values(cursor, sql, rows, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        print(f"  [OK] upserted {len(rows)} deals")


def upsert_orders(cursor, orders: list, login: int):
    """Insert or update trade_orders rows."""
    sql = """
        INSERT INTO trade_orders
            (login, ticket, symbol, type, state,
             volume_initial, volume_current, price_open, price_current,
             sl, tp, comment, time_setup, time_done)
        VALUES %s
        ON CONFLICT (ticket) DO UPDATE SET
            symbol          = EXCLUDED.symbol,
            type            = EXCLUDED.type,
            state           = EXCLUDED.state,
            volume_initial  = EXCLUDED.volume_initial,
            volume_current  = EXCLUDED.volume_current,
            price_open      = EXCLUDED.price_open,
            price_current   = EXCLUDED.price_current,
            sl              = EXCLUDED.sl,
            tp              = EXCLUDED.tp,
            comment         = EXCLUDED.comment,
            time_setup      = EXCLUDED.time_setup,
            time_done       = EXCLUDED.time_done
    """
    rows = []
    for o in orders:
        rows.append((
            login,
            int(o["ticket"]),
            o.get("symbol", ""),
            o.get("type", ""),
            o.get("state", ""),
            float(o.get("volume_initial", 0)),
            float(o.get("volume_current", 0)),
            float(o.get("price_open", 0)),
            float(o.get("price_current", 0)),
            float(o.get("sl", 0)),
            float(o.get("tp", 0)),
            o.get("comment", "") or "",
            _parse_timestamp(o.get("time_setup", "")),
            _parse_timestamp(o.get("time_done")) if o.get("time_done") else None,
        ))
    if rows:
        psycopg2.extras.execute_values(cursor, sql, rows, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        print(f"  [OK] upserted {len(rows)} orders")


def import_csv_to_ohlc(cursor, csv_path: str, symbol: str, timeframe: str):
    """Read an OHLC CSV file and upsert rows into price_ohlc.

    Expected CSV columns (with header):
        time,open,high,low,close,tick_volume,spread,real_volume
    """
    if not os.path.exists(csv_path):
        print(f"  [SKIP] file not found: {csv_path}")
        return

    sql = """
        INSERT INTO price_ohlc
            (symbol, timeframe, datetime, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (symbol, timeframe, datetime) DO NOTHING
    """
    count = 0
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append((
                symbol,
                timeframe,
                _parse_timestamp(row["time"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                int(float(row.get("tick_volume", 0))),
            ))
            count += 1
            # Insert in batches of 500
            if len(rows) >= 500:
                psycopg2.extras.execute_values(
                    cursor, sql, rows,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s)"
                )
                rows.clear()
        if rows:
            psycopg2.extras.execute_values(
                cursor, sql, rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s)"
            )

    print(f"  [OK] imported {count} rows into price_ohlc ({symbol} {timeframe})")


def record_snapshot(cursor, account_info: dict):
    """Insert a trade_account_snapshots row from the account API response."""
    sql = """
        INSERT INTO trade_account_snapshots
            (login, balance, equity, margin, free_margin, margin_level,
             leverage, currency, server, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (
        int(account_info.get("login", 0)),
        float(account_info.get("balance", 0)),
        float(account_info.get("equity", 0)),
        float(account_info.get("margin", 0)),
        float(account_info.get("free_margin", 0)),
        float(account_info.get("margin_level", 0)),
        int(account_info.get("leverage", 0)),
        account_info.get("currency", "USD"),
        account_info.get("server", ""),
        _parse_timestamp(account_info.get("time", "")),
    ))
    print("  [OK] recorded account snapshot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 or SQL-style timestamp string into a datetime.

    Handles:
        - '2026-05-05 06:40:00'              (naive, treated as local)
        - '2026-06-12T18:21:10+00:00'        (UTC with tz offset)
    """
    if not value:
        return datetime.now(timezone.utc)
    # Try ISO-8601 with timezone first
    if "T" in value or "+" in value:
        # Python 3.7+ fromisoformat handles Z and +00:00
        dt = datetime.fromisoformat(value)
        # Convert to naive UTC for TIMESTAMP without timezone
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    # Naive SQL format: '2026-05-05 06:40:00'
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Orchestrate the full ETL pipeline."""
    print("=" * 60)
    print("Trade Analytics Dashboard — ETL Pipeline")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # ---- Connect to PostgreSQL ----
    try:
        print("\n[1] Connecting to PostgreSQL ...")
        conn = psycopg2.connect(**PG_CONFIG)
        conn.autocommit = False
        cursor = conn.cursor()
        print("  [OK] connected")
    except Exception as exc:
        print(f"[FATAL] Cannot connect to PostgreSQL: {exc}")
        sys.exit(1)

    try:
        # ---- Step 2: Account snapshot ----
        print("\n[2] Fetching account info ...")
        try:
            account_info = fetch_api("account")
            if account_info and isinstance(account_info, dict) and account_info.get("login"):
                record_snapshot(cursor, account_info)
            else:
                print("  [SKIP] no account data returned")
        except Exception as exc:
            print(f"  [ERROR] account snapshot failed: {exc}")

        # ---- Step 3: Trade deals ----
        print("\n[3] Fetching trade deals ...")
        try:
            deals_resp = fetch_api("history/deals", {"days": "90", "summary": "true"})
            login = int(account_info.get("login", 0)) if account_info else 0
            if isinstance(deals_resp, dict) and "data" in deals_resp:
                upsert_deals(cursor, deals_resp["data"], login)
            elif isinstance(deals_resp, list):
                upsert_deals(cursor, deals_resp, login)
            else:
                print("  [SKIP] unexpected deals response format")
        except Exception as exc:
            print(f"  [ERROR] deals upsert failed: {exc}")

        # ---- Step 4: Trade orders ----
        print("\n[4] Fetching trade orders ...")
        try:
            orders_resp = fetch_api("history/orders", {"days": "90"})
            if isinstance(orders_resp, list):
                upsert_orders(cursor, orders_resp, login)
            else:
                print("  [SKIP] unexpected orders response format")
        except Exception as exc:
            print(f"  [ERROR] orders upsert failed: {exc}")

        # ---- Step 5: OHLC CSV — H1 ----
        print("\n[5] Importing XAUUSDm H1 CSV ...")
        try:
            csv_h1 = os.path.join(HISTORY_DIR, "XAUUSDm_H1.csv")
            import_csv_to_ohlc(cursor, csv_h1, "XAUUSDm", "H1")
        except Exception as exc:
            print(f"  [ERROR] H1 import failed: {exc}")

        # ---- Step 6: OHLC CSV — M5 ----
        print("\n[6] Importing XAUUSDm M5 CSV ...")
        try:
            csv_m5 = os.path.join(HISTORY_DIR, "XAUUSDm_M5.csv")
            import_csv_to_ohlc(cursor, csv_m5, "XAUUSDm", "M5")
        except Exception as exc:
            print(f"  [ERROR] M5 import failed: {exc}")

        # ---- Commit ----
        print("\n[7] Committing transaction ...")
        conn.commit()
        print("  [OK] committed")

    except Exception as exc:
        print(f"\n[FATAL] Unexpected error, rolling back: {exc}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()
        print("\n[✓] ETL pipeline finished. Connection closed.")


if __name__ == "__main__":
    main()
