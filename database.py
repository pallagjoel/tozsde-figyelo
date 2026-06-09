"""
database.py — SQLite Database Layer for Tőzsde Figyelő
Handles all persistent storage of stock data, history, and cache.
"""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "market_data.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # conn.execute("PRAGMA foreign_keys=ON")  # Disabled due to composite PK mismatch in legacy tables
    return conn


def init_db():
    """Initialize database schema if not already created."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS tracked_stocks (
            user_id         INTEGER,
            ticker          TEXT,
            name            TEXT,
            currency        TEXT,
            sector          TEXT,
            industry        TEXT,
            exchange        TEXT,
            country         TEXT,
            website         TEXT,
            description     TEXT,
            market_cap      REAL,
            employees       INTEGER,
            added_at        TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, ticker)
        );

        CREATE TABLE IF NOT EXISTS stock_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            date            TEXT NOT NULL,
            open            REAL,
            high            REAL,
            low             REAL,
            close           REAL,
            volume          INTEGER,
            UNIQUE(ticker, date),
            FOREIGN KEY (ticker) REFERENCES tracked_stocks(ticker) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_history_ticker_date ON stock_history(ticker, date);

        CREATE TABLE IF NOT EXISTS stock_cache (
            ticker          TEXT PRIMARY KEY,
            current_price   REAL,
            previous_close  REAL,
            day_open        REAL,
            day_high        REAL,
            day_low         REAL,
            volume          INTEGER,
            market_cap      REAL,
            pe_ratio        REAL,
            eps             REAL,
            dividend_yield  REAL,
            fifty_two_week_high REAL,
            fifty_two_week_low  REAL,
            beta            REAL,
            last_updated    TEXT,
            FOREIGN KEY (ticker) REFERENCES tracked_stocks(ticker) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


# ── Tracked Stocks ──────────────────────────────────────────────────────────

def add_tracked_stock(user_id: int, info: dict):
    """Insert or update a tracked stock's metadata."""
    conn = get_connection()
    info["user_id"] = user_id
    conn.execute("""
        INSERT INTO tracked_stocks
            (user_id, ticker, name, currency, sector, industry, exchange, country, website, description, market_cap, employees)
        VALUES
            (:user_id, :ticker, :name, :currency, :sector, :industry, :exchange, :country, :website, :description, :market_cap, :employees)
        ON CONFLICT(user_id, ticker) DO UPDATE SET
            name=excluded.name, currency=excluded.currency, sector=excluded.sector,
            industry=excluded.industry, exchange=excluded.exchange, country=excluded.country,
            website=excluded.website, description=excluded.description,
            market_cap=excluded.market_cap, employees=excluded.employees
    """, info)
    conn.commit()
    conn.close()


def remove_tracked_stock(user_id: int, ticker: str):
    """Remove a stock from tracking (cascades to history and cache)."""
    conn = get_connection()
    conn.execute("DELETE FROM tracked_stocks WHERE user_id = ? AND ticker = ?", (user_id, ticker.upper()))
    conn.commit()
    conn.close()


def get_tracked_stocks(user_id: int) -> list[dict]:
    """Return all tracked stocks with their cached price data for a specific user."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ts.*, sc.current_price, sc.previous_close, sc.day_high, sc.day_low,
               sc.volume, sc.pe_ratio, sc.eps, sc.dividend_yield, sc.beta,
               sc.fifty_two_week_high, sc.fifty_two_week_low, sc.last_updated
        FROM tracked_stocks ts
        LEFT JOIN stock_cache sc ON ts.ticker = sc.ticker
        WHERE ts.user_id = ?
        ORDER BY ts.added_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def stock_exists(user_id: int, ticker: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM tracked_stocks WHERE user_id = ? AND ticker = ?", (user_id, ticker.upper())).fetchone()
    conn.close()
    return row is not None


# ── Stock History ────────────────────────────────────────────────────────────

def upsert_stock_history(ticker: str, records: list[dict]):
    """Bulk insert or replace OHLCV history records."""
    conn = get_connection()
    conn.executemany("""
        INSERT OR REPLACE INTO stock_history (ticker, date, open, high, low, close, volume)
        VALUES (:ticker, :date, :open, :high, :low, :close, :volume)
    """, records)
    conn.commit()
    conn.close()


def get_stock_history(ticker: str, limit: int = 365) -> list[dict]:
    """Retrieve OHLCV history for a ticker, most recent first."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM stock_history
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT ?
    """, (ticker.upper(), limit)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]  # Return chronological order


# ── Stock Cache (Current Prices) ─────────────────────────────────────────────

def upsert_stock_cache(ticker: str, data: dict):
    """Insert or update the live price cache for a stock."""
    conn = get_connection()
    data["ticker"] = ticker.upper()
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO stock_cache
            (ticker, current_price, previous_close, day_open, day_high, day_low,
             volume, market_cap, pe_ratio, eps, dividend_yield,
             fifty_two_week_high, fifty_two_week_low, beta, last_updated)
        VALUES
            (:ticker, :current_price, :previous_close, :day_open, :day_high, :day_low,
             :volume, :market_cap, :pe_ratio, :eps, :dividend_yield,
             :fifty_two_week_high, :fifty_two_week_low, :beta, :last_updated)
        ON CONFLICT(ticker) DO UPDATE SET
            current_price=excluded.current_price, previous_close=excluded.previous_close,
            day_open=excluded.day_open, day_high=excluded.day_high, day_low=excluded.day_low,
            volume=excluded.volume, market_cap=excluded.market_cap, pe_ratio=excluded.pe_ratio,
            eps=excluded.eps, dividend_yield=excluded.dividend_yield,
            fifty_two_week_high=excluded.fifty_two_week_high, fifty_two_week_low=excluded.fifty_two_week_low,
            beta=excluded.beta, last_updated=excluded.last_updated
    """, data)
    conn.commit()
    conn.close()
