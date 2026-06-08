"""
async_etl.py — High-Performance Async ETL Pipeline for Tőzsde Figyelő
Designed for 1,000+ tickers with concurrent, non-blocking API requests.

Features:
    - asyncio + aiohttp for concurrent API calls
    - Centralized AsyncRateLimiter with exponential backoff + jitter
    - SQLAlchemy bulk insert/upsert operations (batch writes)
    - Configurable concurrency per API provider (from config.yaml)
    - Resilient per-ticker error handling (one failure doesn't crash the batch)

Usage:
    # As a script
    python async_etl.py AAPL MSFT TSLA
    python async_etl.py --all

    # As a module
    from async_etl import run_async_etl
    asyncio.run(run_async_etl(["AAPL", "MSFT"]))
"""

from __future__ import annotations

import asyncio
import os
import random
import time
import traceback
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import aiohttp
import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import insert, inspect
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from models import (
    engine, get_session, init_quant_db, Base,
    Company, QuarterlyFinancials, DailyPrice, MacroRate,
    get_or_create_company,
)
from math_config import get_config

# ── Load Environment ──────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FMP_API_KEY  = os.getenv("FMP_API_KEY", "")
FMP_BASE     = "https://financialmodelingprep.com/api/v3"


def _safe(val, default=None):
    if val is None: return default
    try:
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC RATE LIMITER (with exponential backoff + jitter)
# ══════════════════════════════════════════════════════════════════════════════

class AsyncRateLimiter:
    """
    Centralized rate limiter for async API calls.
    - Enforces max concurrency via semaphore
    - Enforces min delay between requests via async lock
    - Implements exponential backoff with jitter on failure
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 5,
        base_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
    ):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._request_count = 0

    async def execute(self, coro_factory, *args, **kwargs):
        """
        Execute an async callable with rate limiting and retry logic.

        Args:
            coro_factory: An async function to call.
            *args, **kwargs: Arguments to pass to the function.

        Returns:
            The result of the async function.

        Raises:
            Last exception if all retries are exhausted.
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            async with self.semaphore:
                # Enforce minimum delay between requests
                async with self._lock:
                    now = time.monotonic()
                    elapsed = now - self._last_request_time
                    if elapsed < self.base_delay:
                        await asyncio.sleep(self.base_delay - elapsed)
                    self._last_request_time = time.monotonic()
                    self._request_count += 1

                try:
                    result = await coro_factory(*args, **kwargs)
                    return result

                except Exception as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        # Exponential backoff with jitter
                        delay = min(
                            self.backoff_base ** attempt + random.uniform(0, 1),
                            self.backoff_max,
                        )
                        print(f"    [{self.name}] Retry {attempt+1}/{self.max_retries} "
                              f"in {delay:.1f}s: {e}")
                        await asyncio.sleep(delay)
                    else:
                        print(f"    [{self.name}] All {self.max_retries} retries exhausted: {e}")

        raise last_exception

    @property
    def stats(self) -> dict:
        return {"name": self.name, "total_requests": self._request_count}


# ══════════════════════════════════════════════════════════════════════════════
# BULK DATABASE OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

class BulkDBWriter:
    """
    Batch database writer using SQLAlchemy Core for high-throughput inserts.
    Uses INSERT ... ON CONFLICT DO UPDATE (SQLite upsert) for idempotency.
    Buffers rows and flushes in configurable batch sizes.
    """

    def __init__(self, batch_size: int = 500):
        self.batch_size = batch_size
        self._daily_price_buffer: list[dict] = []
        self._quarterly_buffer: list[dict] = []
        self._flush_count = 0

    def buffer_daily_price(self, row: dict):
        """Add a daily price row to the buffer."""
        self._daily_price_buffer.append(row)
        if len(self._daily_price_buffer) >= self.batch_size:
            self.flush_daily_prices()

    def buffer_quarterly(self, row: dict):
        """Add a quarterly financial row to the buffer."""
        self._quarterly_buffer.append(row)
        if len(self._quarterly_buffer) >= self.batch_size:
            self.flush_quarterlies()

    def flush_daily_prices(self):
        """Bulk upsert daily prices to database."""
        if not self._daily_price_buffer:
            return

        session = get_session()
        try:
            for row in self._daily_price_buffer:
                stmt = sqlite_upsert(DailyPrice).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["company_id", "date"],
                    set_={
                        "close": stmt.excluded.close,
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "volume": stmt.excluded.volume,
                    }
                )
                session.execute(stmt)
            session.commit()
            self._flush_count += len(self._daily_price_buffer)
        except Exception as e:
            session.rollback()
            print(f"    [BulkDB] Daily price flush error: {e}")
        finally:
            self._daily_price_buffer.clear()
            session.close()

    def flush_quarterlies(self):
        """Bulk upsert quarterly financials."""
        if not self._quarterly_buffer:
            return

        session = get_session()
        try:
            for row in self._quarterly_buffer:
                stmt = sqlite_upsert(QuarterlyFinancials).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["company_id", "period_end"],
                    set_={k: stmt.excluded[k] for k in row if k not in ("company_id", "period_end", "id")}
                )
                session.execute(stmt)
            session.commit()
            self._flush_count += len(self._quarterly_buffer)
        except Exception as e:
            session.rollback()
            print(f"    [BulkDB] Quarterly flush error: {e}")
        finally:
            self._quarterly_buffer.clear()
            session.close()

    def flush_all(self):
        """Flush all pending buffers."""
        self.flush_daily_prices()
        self.flush_quarterlies()

    @property
    def stats(self) -> dict:
        return {
            "flushed_rows": self._flush_count,
            "pending_prices": len(self._daily_price_buffer),
            "pending_quarterlies": len(self._quarterly_buffer),
        }


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC API CLIENTS
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_fmp_json(session_http: aiohttp.ClientSession, url: str) -> list | dict:
    """Fetch JSON from FMP API with error handling."""
    async with session_http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status == 429:
            raise Exception("FMP rate limit hit (429)")
        resp.raise_for_status()
        data = await resp.json()
        if isinstance(data, dict) and "Error Message" in data:
            raise Exception(f"FMP: {data['Error Message']}")
        return data


async def etl_fmp_financials(
    session_http: aiohttp.ClientSession,
    limiter: AsyncRateLimiter,
    ticker: str,
    company_id: int,
    bulk_writer: BulkDBWriter,
) -> dict:
    """Async: Pull quarterly financials from FMP for one ticker."""
    result = {"ticker": ticker, "quarters": 0, "source": "fmp"}

    if not FMP_API_KEY:
        return {"ticker": ticker, "quarters": 0, "source": "skipped_no_key"}

    try:
        # Fetch income, balance sheet, cash flow concurrently
        income_task = limiter.execute(
            fetch_fmp_json, session_http,
            f"{FMP_BASE}/income-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        )
        bs_task = limiter.execute(
            fetch_fmp_json, session_http,
            f"{FMP_BASE}/balance-sheet-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        )
        cf_task = limiter.execute(
            fetch_fmp_json, session_http,
            f"{FMP_BASE}/cash-flow-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        )

        income_data, bs_data, cf_data = await asyncio.gather(
            income_task, bs_task, cf_task, return_exceptions=True
        )

        # Handle partial failures
        if isinstance(income_data, Exception):
            income_data = []
        if isinstance(bs_data, Exception):
            bs_data = []
        if isinstance(cf_data, Exception):
            cf_data = []

        if not income_data or not isinstance(income_data, list):
            return result

        bs_map = {item.get("date", ""): item for item in (bs_data if isinstance(bs_data, list) else [])}
        cf_map = {item.get("date", ""): item for item in (cf_data if isinstance(cf_data, list) else [])}

        for inc in income_data:
            period_str = inc.get("date", "")
            if not period_str:
                continue
            try:
                period_date = date.fromisoformat(period_str)
            except (ValueError, TypeError):
                continue

            bs = bs_map.get(period_str, {})
            cf = cf_map.get(period_str, {})

            quarter_str = inc.get("period", "")
            fiscal_q = None
            for q in [1, 2, 3, 4]:
                if f"Q{q}" in quarter_str.upper():
                    fiscal_q = q
                    break

            row = {
                "company_id": company_id,
                "period_end": period_date,
                "fiscal_year": inc.get("calendarYear"),
                "fiscal_quarter": fiscal_q,
                "revenue": _safe(inc.get("revenue")),
                "cost_of_revenue": _safe(inc.get("costOfRevenue")),
                "gross_profit": _safe(inc.get("grossProfit")),
                "operating_income": _safe(inc.get("operatingIncome")),
                "ebit": _safe(inc.get("operatingIncome")),
                "ebitda": _safe(inc.get("ebitda")),
                "net_income": _safe(inc.get("netIncome")),
                "interest_expense": _safe(inc.get("interestExpense")),
                "income_tax_expense": _safe(inc.get("incomeTaxExpense")),
                "total_assets": _safe(bs.get("totalAssets")),
                "total_liabilities": _safe(bs.get("totalLiabilities")),
                "current_assets": _safe(bs.get("totalCurrentAssets")),
                "current_liabilities": _safe(bs.get("totalCurrentLiabilities")),
                "total_equity": _safe(bs.get("totalStockholdersEquity")),
                "retained_earnings": _safe(bs.get("retainedEarnings")),
                "total_debt": _safe(bs.get("totalDebt")) or _safe(bs.get("longTermDebt")),
                "cash_and_equivalents": _safe(bs.get("cashAndCashEquivalents")),
                "operating_cash_flow": _safe(cf.get("operatingCashFlow")),
                "capital_expenditure": _safe(cf.get("capitalExpenditure")),
                "free_cash_flow": _safe(cf.get("freeCashFlow")),
                "depreciation": _safe(cf.get("depreciationAndAmortization")),
                "data_source": "fmp",
            }
            bulk_writer.buffer_quarterly(row)
            result["quarters"] += 1

    except Exception as e:
        result["error"] = str(e)

    return result


async def etl_yfinance_prices(
    ticker: str,
    company_id: int,
    bulk_writer: BulkDBWriter,
    period: str = "2y",
) -> dict:
    """Fetch daily OHLCV from yfinance (runs in thread pool to avoid blocking)."""
    result = {"ticker": ticker, "days": 0}

    try:
        loop = asyncio.get_event_loop()
        # yfinance is synchronous — run in thread pool
        t = await loop.run_in_executor(None, lambda: yf.Ticker(ticker))
        df = await loop.run_in_executor(None, lambda: t.history(period=period, auto_adjust=True))

        if df.empty:
            return result

        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        for _, row in df.iterrows():
            date_val = row.get("date") or row.get("datetime")
            if hasattr(date_val, "date"):
                price_date = date_val.date()
            else:
                try:
                    price_date = pd.to_datetime(date_val).date()
                except Exception:
                    continue

            dp_row = {
                "company_id": company_id,
                "date": price_date,
                "open": _safe(row.get("open")),
                "high": _safe(row.get("high")),
                "low": _safe(row.get("low")),
                "close": _safe(row.get("close")),
                "volume": int(row["volume"]) if pd.notna(row.get("volume")) else None,
                "adj_close": _safe(row.get("close")),
            }
            bulk_writer.buffer_daily_price(dp_row)
            result["days"] += 1

        # Also update company metadata
        info = await loop.run_in_executor(None, lambda: t.info or {})
        session = get_session()
        try:
            company = session.query(Company).get(company_id)
            if company:
                company.name = info.get("longName") or info.get("shortName") or company.name
                company.sector = info.get("sector") or company.sector
                company.industry = info.get("industry") or company.industry
                company.country = info.get("country") or company.country
                company.shares_outstanding = _safe(info.get("sharesOutstanding")) or company.shares_outstanding
                company.market_cap = _safe(info.get("marketCap")) or company.market_cap
                company.beta = _safe(info.get("beta")) or company.beta
                session.commit()
        except Exception as e:
            session.rollback()
        finally:
            session.close()

    except Exception as e:
        result["error"] = str(e)

    return result


async def etl_yfinance_financials_fallback(
    ticker: str,
    company_id: int,
    bulk_writer: BulkDBWriter,
) -> dict:
    """Fallback: use yfinance for quarterly financials when FMP key is missing."""
    result = {"ticker": ticker, "quarters": 0, "source": "yfinance"}

    try:
        loop = asyncio.get_event_loop()
        t = await loop.run_in_executor(None, lambda: yf.Ticker(ticker))

        inc_q = await loop.run_in_executor(None, lambda: getattr(t, "quarterly_income_stmt", pd.DataFrame()))
        bs_q  = await loop.run_in_executor(None, lambda: getattr(t, "quarterly_balance_sheet", pd.DataFrame()))
        cf_q  = await loop.run_in_executor(None, lambda: getattr(t, "quarterly_cashflow", pd.DataFrame()))

        if isinstance(inc_q, pd.DataFrame) and not inc_q.empty:
            for col_date in inc_q.columns[:8]:
                try:
                    if hasattr(col_date, "date"):
                        period_date = col_date.date()
                    else:
                        period_date = pd.to_datetime(col_date).date()
                except Exception:
                    continue

                def yf_val(df, *keys):
                    if not isinstance(df, pd.DataFrame) or df.empty or col_date not in df.columns:
                        return None
                    for key in keys:
                        if key in df.index:
                            return _safe(df.loc[key, col_date])
                    return None

                row = {
                    "company_id": company_id,
                    "period_end": period_date,
                    "revenue": yf_val(inc_q, "Total Revenue", "Revenue"),
                    "cost_of_revenue": yf_val(inc_q, "Cost Of Revenue"),
                    "gross_profit": yf_val(inc_q, "Gross Profit"),
                    "operating_income": yf_val(inc_q, "Operating Income", "EBIT"),
                    "ebit": yf_val(inc_q, "EBIT", "Operating Income"),
                    "ebitda": yf_val(inc_q, "EBITDA", "Normalized EBITDA"),
                    "net_income": yf_val(inc_q, "Net Income", "Net Income Common Stockholders"),
                    "interest_expense": yf_val(inc_q, "Interest Expense", "Interest Expense Non Operating"),
                    "income_tax_expense": yf_val(inc_q, "Tax Provision", "Income Tax Expense"),
                    "total_assets": yf_val(bs_q, "Total Assets"),
                    "total_liabilities": yf_val(bs_q, "Total Liabilities Net Minority Interest", "Total Liabilities"),
                    "current_assets": yf_val(bs_q, "Current Assets", "Total Current Assets"),
                    "current_liabilities": yf_val(bs_q, "Current Liabilities", "Total Current Liabilities"),
                    "total_equity": yf_val(bs_q, "Stockholders Equity", "Total Stockholders Equity"),
                    "retained_earnings": yf_val(bs_q, "Retained Earnings"),
                    "total_debt": yf_val(bs_q, "Total Debt", "Long Term Debt"),
                    "cash_and_equivalents": yf_val(bs_q, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
                    "operating_cash_flow": yf_val(cf_q, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
                    "capital_expenditure": yf_val(cf_q, "Capital Expenditure"),
                    "free_cash_flow": yf_val(cf_q, "Free Cash Flow"),
                    "depreciation": yf_val(cf_q, "Depreciation And Amortization"),
                    "data_source": "yfinance",
                }
                bulk_writer.buffer_quarterly(row)
                result["quarters"] += 1

    except Exception as e:
        result["error"] = str(e)

    return result


async def etl_fred_macro() -> dict:
    """Fetch macro rates from FRED API (async-compatible wrapper)."""
    result = {"status": "ok", "source": "FRED"}
    today = date.today()

    if not FRED_API_KEY:
        # Fallback
        session = get_session()
        try:
            existing = session.query(MacroRate).filter(MacroRate.date == today).first()
            if not existing:
                session.add(MacroRate(
                    date=today, risk_free_rate=4.25, market_return=10.0,
                    inflation_rate=3.0, equity_risk_premium=5.75, fed_funds_rate=5.25,
                ))
                session.commit()
            result["source"] = "fallback"
        finally:
            session.close()
        return result

    try:
        from fredapi import Fred
        loop = asyncio.get_event_loop()
        fred = Fred(api_key=FRED_API_KEY)

        dgs10 = await loop.run_in_executor(
            None, lambda: fred.get_series("DGS10", observation_start=(today - timedelta(days=14)))
        )
        rf = float(dgs10.dropna().iloc[-1]) if not dgs10.dropna().empty else 4.25

        fedfunds = await loop.run_in_executor(
            None, lambda: fred.get_series("FEDFUNDS", observation_start=(today - timedelta(days=60)))
        )
        ffr = float(fedfunds.dropna().iloc[-1]) if not fedfunds.dropna().empty else 5.25

        cpi = await loop.run_in_executor(
            None, lambda: fred.get_series("CPIAUCSL", observation_start=(today - timedelta(days=400)))
        )
        if len(cpi.dropna()) >= 12:
            inflation = ((cpi.dropna().iloc[-1] / cpi.dropna().iloc[-12]) - 1) * 100
        else:
            inflation = 3.0

        session = get_session()
        try:
            existing = session.query(MacroRate).filter(MacroRate.date == today).first()
            macro = existing or MacroRate(date=today)
            macro.risk_free_rate = round(rf, 4)
            macro.market_return = 10.0
            macro.inflation_rate = round(inflation, 4)
            macro.equity_risk_premium = round(10.0 - rf, 4)
            macro.fed_funds_rate = round(ffr, 4)
            if not existing:
                session.add(macro)
            session.commit()
        finally:
            session.close()

        print(f"  [FRED] ✓ Rf={rf:.2f}% | FFR={ffr:.2f}% | CPI={inflation:.2f}%")

    except Exception as e:
        print(f"  [FRED] ✗ Error: {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC ETL ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

async def process_single_ticker(
    ticker: str,
    session_http: aiohttp.ClientSession,
    fmp_limiter: AsyncRateLimiter,
    yf_limiter: AsyncRateLimiter,
    bulk_writer: BulkDBWriter,
) -> dict:
    """Process a single ticker: create company → fetch financials → fetch prices."""
    result = {"ticker": ticker, "status": "ok"}

    # Ensure Company record exists
    session = get_session()
    try:
        company = get_or_create_company(session, ticker)
        session.commit()
        company_id = company.id
    except Exception as e:
        session.rollback()
        result["status"] = "error"
        result["error"] = f"Company creation failed: {e}"
        return result
    finally:
        session.close()

    # Fetch financials (FMP or yfinance fallback)
    if FMP_API_KEY:
        fin_result = await etl_fmp_financials(session_http, fmp_limiter, ticker, company_id, bulk_writer)
    else:
        fin_result = await yf_limiter.execute(
            etl_yfinance_financials_fallback, ticker, company_id, bulk_writer
        )
    result["financials"] = fin_result

    # Fetch daily prices (yfinance, via thread pool)
    price_result = await yf_limiter.execute(
        etl_yfinance_prices, ticker, company_id, bulk_writer
    )
    result["prices"] = price_result

    return result


async def run_async_etl(tickers: Optional[list[str]] = None) -> dict:
    """
    High-performance async ETL pipeline for 1,000+ tickers.

    Pipeline:
        1. FRED macro rates (single call)
        2. Concurrent per-ticker ETL (FMP financials + yfinance prices)
        3. Bulk database flushes
    """
    init_quant_db()
    cfg = get_config()
    results = {"macro": {}, "tickers": [], "errors": [], "stats": {}}
    start_time = time.monotonic()

    # Determine ticker universe
    if not tickers:
        session = get_session()
        try:
            companies = session.query(Company).filter(Company.is_active == True).all()
            tickers = [c.ticker for c in companies]
            # Also check legacy DB
            try:
                import database as legacy_db
                for s in legacy_db.get_tracked_stocks():
                    if s["ticker"] not in tickers:
                        tickers.append(s["ticker"])
            except Exception:
                pass
        finally:
            session.close()

    if not tickers:
        print("  ⚠ No tickers to process.")
        return results

    print(f"\n{'='*70}")
    print(f"  ASYNC ETL — Processing {len(tickers)} tickers")
    print(f"  FMP Key: {'✓' if FMP_API_KEY else '✗ (using yfinance fallback)'}")
    print(f"  FRED Key: {'✓' if FRED_API_KEY else '✗ (using fallback rates)'}")
    print(f"{'='*70}")

    # Create rate limiters from config
    fmp_cfg = cfg.api_limits_for("fmp")
    yf_cfg  = cfg.api_limits_for("yfinance")

    fmp_limiter = AsyncRateLimiter(
        name="FMP",
        max_concurrent=fmp_cfg.get("max_concurrent", 5),
        base_delay=fmp_cfg.get("base_delay_seconds", 0.5),
        max_retries=fmp_cfg.get("max_retries", 3),
        backoff_base=fmp_cfg.get("backoff_base", 2.0),
        backoff_max=fmp_cfg.get("backoff_max_seconds", 60),
    )

    yf_limiter = AsyncRateLimiter(
        name="yfinance",
        max_concurrent=yf_cfg.get("max_concurrent", 10),
        base_delay=yf_cfg.get("base_delay_seconds", 0.2),
        max_retries=yf_cfg.get("max_retries", 3),
        backoff_base=yf_cfg.get("backoff_base", 1.5),
        backoff_max=yf_cfg.get("backoff_max_seconds", 15),
    )

    bulk_writer = BulkDBWriter(
        batch_size=cfg.api_limit("bulk_db", "batch_size", 500)
    )

    # Step 1: Macro rates
    print("\n  [1/2] Fetching FRED macro rates...")
    results["macro"] = await etl_fred_macro()

    # Step 2: Process tickers concurrently
    print(f"\n  [2/2] Processing {len(tickers)} tickers concurrently...")

    async with aiohttp.ClientSession() as session_http:
        # Process in batches to avoid overwhelming APIs
        batch_size = fmp_cfg.get("max_concurrent", 5) * 2
        for batch_start in range(0, len(tickers), batch_size):
            batch = tickers[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (len(tickers) + batch_size - 1) // batch_size
            print(f"\n  ── Batch {batch_num}/{total_batches}: {', '.join(batch)} ──")

            tasks = [
                process_single_ticker(t, session_http, fmp_limiter, yf_limiter, bulk_writer)
                for t in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for t, r in zip(batch, batch_results):
                if isinstance(r, Exception):
                    results["errors"].append({"ticker": t, "error": str(r)})
                    print(f"    [{t}] ✗ {r}")
                else:
                    results["tickers"].append(r)
                    fin_q = r.get("financials", {}).get("quarters", 0)
                    price_d = r.get("prices", {}).get("days", 0)
                    print(f"    [{t}] ✓ {fin_q}Q + {price_d}D")

            # Flush after each batch
            bulk_writer.flush_all()

    # Final flush
    bulk_writer.flush_all()

    elapsed = time.monotonic() - start_time
    results["stats"] = {
        "elapsed_seconds": round(elapsed, 2),
        "tickers_processed": len(results["tickers"]),
        "errors": len(results["errors"]),
        "db_writes": bulk_writer.stats,
        "fmp_requests": fmp_limiter.stats,
        "yf_requests": yf_limiter.stats,
    }

    print(f"\n{'='*70}")
    print(f"  ASYNC ETL COMPLETE in {elapsed:.1f}s")
    print(f"  Processed: {len(results['tickers'])} | Errors: {len(results['errors'])}")
    print(f"  DB Writes: {bulk_writer.stats['flushed_rows']} rows flushed")
    print(f"{'='*70}\n")

    return results


# ── Sync wrapper for use from FastAPI ─────────────────────────────────────────

def run_async_etl_sync(tickers: Optional[list[str]] = None) -> dict:
    """Synchronous wrapper to run async ETL from non-async contexts."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an already-running loop (e.g., FastAPI)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, run_async_etl(tickers))
                return future.result()
        else:
            return loop.run_until_complete(run_async_etl(tickers))
    except RuntimeError:
        return asyncio.run(run_async_etl(tickers))


# ── CLI Entry Point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else None
    if tickers and tickers[0] == "--all":
        tickers = None
    asyncio.run(run_async_etl(tickers))
