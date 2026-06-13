-- ============================================================================
-- Trade Analytics Dashboard — Schema DDL
-- Phase 1: PostgreSQL Schema
-- ============================================================================

-- trade_account_snapshots: 帳戶快照 (balance, equity, margin, etc.)
CREATE TABLE IF NOT EXISTS trade_account_snapshots (
    id BIGSERIAL PRIMARY KEY,
    login BIGINT NOT NULL,
    balance DOUBLE PRECISION NOT NULL,
    equity DOUBLE PRECISION NOT NULL,
    margin DOUBLE PRECISION NOT NULL DEFAULT 0,
    free_margin DOUBLE PRECISION NOT NULL DEFAULT 0,
    margin_level DOUBLE PRECISION NOT NULL DEFAULT 0,
    leverage INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
    server VARCHAR(100) NOT NULL DEFAULT '',
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- trade_deals: 成交明細
CREATE TABLE IF NOT EXISTS trade_deals (
    id BIGSERIAL PRIMARY KEY,
    login BIGINT NOT NULL,
    deal_id BIGINT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    type VARCHAR(20) NOT NULL,
    entry VARCHAR(10) NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    profit DOUBLE PRECISION NOT NULL,
    swap DOUBLE PRECISION NOT NULL DEFAULT 0,
    commission DOUBLE PRECISION NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    position_id BIGINT NOT NULL DEFAULT 0,
    order_id BIGINT NOT NULL DEFAULT 0,
    deal_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(deal_id)
);

-- trade_orders: 歷史委託
CREATE TABLE IF NOT EXISTS trade_orders (
    id BIGSERIAL PRIMARY KEY,
    login BIGINT NOT NULL,
    ticket BIGINT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    type VARCHAR(30) NOT NULL,
    state VARCHAR(20) NOT NULL,
    volume_initial DOUBLE PRECISION NOT NULL DEFAULT 0,
    volume_current DOUBLE PRECISION NOT NULL DEFAULT 0,
    price_open DOUBLE PRECISION NOT NULL DEFAULT 0,
    price_current DOUBLE PRECISION NOT NULL DEFAULT 0,
    sl DOUBLE PRECISION NOT NULL DEFAULT 0,
    tp DOUBLE PRECISION NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    time_setup TIMESTAMP NOT NULL,
    time_done TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(ticket)
);

-- price_ohlc: OHLC 價格 (支援多 symbol + 多 timeframe)
CREATE TABLE IF NOT EXISTS price_ohlc (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(5) NOT NULL,
    datetime TIMESTAMP NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(symbol, timeframe, datetime)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_deals_login ON trade_deals(login);
CREATE INDEX IF NOT EXISTS idx_deals_time ON trade_deals(deal_time);
CREATE INDEX IF NOT EXISTS idx_deals_symbol ON trade_deals(symbol);
CREATE INDEX IF NOT EXISTS idx_ohlc_lookup ON price_ohlc(symbol, timeframe, datetime);
CREATE INDEX IF NOT EXISTS idx_snapshots_login ON trade_account_snapshots(login, recorded_at);
