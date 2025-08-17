CREATE TABLE IF NOT EXISTS cycles (
    cycle_id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    winner_bot_id INTEGER,
    winner_reason TEXT
);

CREATE TABLE IF NOT EXISTS bots (
    bot_id INTEGER PRIMARY KEY,
    cycle_id INTEGER,
    name TEXT,
    seed_parent TEXT,
    mutations_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_stats (
    bot_id INTEGER,
    cycle_id INTEGER,
    orders INTEGER,
    buys INTEGER,
    sells INTEGER,
    pnl REAL,
    pnl_pct REAL,
    runtime_s INTEGER,
    wins INTEGER,
    losses INTEGER,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bot_id, cycle_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    bot_id INTEGER,
    cycle_id INTEGER,
    symbol TEXT,
    side TEXT,
    qty REAL,
    price REAL,
    resulting_fill_price REAL,
    fee_asset TEXT,
    fee_amount REAL,
    ts TEXT,
    status TEXT,
    pnl REAL,
    pnl_pct REAL,
    notes TEXT,
    raw_json TEXT,
    expected_profit_ticks INTEGER,
    actual_profit_ticks INTEGER,
    spread_ticks REAL,
    imbalance_pct REAL,
    top3_depth TEXT,
    book_hash TEXT,
    latency_ms INTEGER,
    cancel_replace_count INTEGER,
    time_in_force TEXT,
    hold_time_s REAL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    level TEXT,
    scope TEXT,
    bot_id INTEGER,
    cycle_id INTEGER,
    message TEXT,
    payload_json TEXT
);
