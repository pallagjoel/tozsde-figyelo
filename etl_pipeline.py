"""
etl_pipeline.py — Extract-Transform-Load Pipeline for Tőzsde Figyelő
Decoupled ETL process respecting API rate limits.

Phase A (Nightly Batch):
    1. FRED API  → MacroRate table (risk-free rate, inflation)
    2. FMP API   → QuarterlyFinancials table (SEC-level data)
    3. yfinance   → DailyPrice table (OHLCV) + Company metadata

Fallback: If FRED/FMP keys are missing, uses yfinance for basic fundamentals.
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import pandas as pd
import numpy as np
import requests
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models import (
    Base, engine, get_session, init_quant_db,
    Company, QuarterlyFinancials, DailyPrice, MacroRate,
    get_or_create_company,
)

# ── Load Environment ──────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FMP_API_KEY  = os.getenv("FMP_API_KEY", "")

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
FMP_DELAY    = 0.5  # seconds between FMP calls (respect rate limit: 250/day)


def _safe(val, default=None):
    """Return val if it's a real number, else default."""
    if val is None:
        return default
    try:
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A.1: FRED API — Macro Rates
# ══════════════════════════════════════════════════════════════════════════════

def etl_macro_rates(session: Session) -> dict:
    """
    Pull macroeconomic rates from FRED API:
        - DGS10:    10-Year Treasury Constant Maturity (Risk-Free Rate)
        - FEDFUNDS: Effective Federal Funds Rate
        - CPIAUCSL: CPI for All Urban Consumers (for inflation)

    Falls back to hardcoded defaults if FRED key is missing.
    """
    result = {"status": "ok", "source": "FRED"}
    today = date.today()

    if not FRED_API_KEY:
        print("  [FRED] ⚠ No API key — using fallback macro rates.")
        # Insert fallback macro rate
        macro = MacroRate(
            date=today,
            risk_free_rate=4.25,       # approximate current 10Y
            market_return=10.0,        # long-run S&P 500
            inflation_rate=3.0,        # approx CPI
            equity_risk_premium=5.75,  # Rm - Rf
            fed_funds_rate=5.25,       # approx
        )
        _upsert_macro(session, macro)
        result["source"] = "fallback"
        return result

    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)

        # 10-Year Treasury yield
        dgs10 = fred.get_series("DGS10", observation_start=(today - timedelta(days=14)))
        rf = float(dgs10.dropna().iloc[-1]) if not dgs10.dropna().empty else 4.25
        print(f"  [FRED] 10Y Treasury yield: {rf:.2f}%")

        # Fed Funds Rate
        fedfunds = fred.get_series("FEDFUNDS", observation_start=(today - timedelta(days=60)))
        ffr = float(fedfunds.dropna().iloc[-1]) if not fedfunds.dropna().empty else 5.25
        print(f"  [FRED] Fed Funds Rate: {ffr:.2f}%")

        # CPI YoY (monthly, so get last 13 months for YoY calc)
        cpi = fred.get_series("CPIAUCSL", observation_start=(today - timedelta(days=400)))
        if len(cpi.dropna()) >= 12:
            cpi_clean = cpi.dropna()
            inflation = ((cpi_clean.iloc[-1] / cpi_clean.iloc[-12]) - 1) * 100
        else:
            inflation = 3.0
        print(f"  [FRED] CPI YoY Inflation: {inflation:.2f}%")

        # Long-run market return (use S&P 500 historical average ~10%)
        market_return = 10.0
        erp = market_return - rf

        macro = MacroRate(
            date=today,
            risk_free_rate=round(rf, 4),
            market_return=round(market_return, 4),
            inflation_rate=round(inflation, 4),
            equity_risk_premium=round(erp, 4),
            fed_funds_rate=round(ffr, 4),
        )
        _upsert_macro(session, macro)
        print(f"  [FRED] ✓ Macro rates saved for {today}")

    except Exception as e:
        print(f"  [FRED] ✗ Error: {e}")
        # Fallback
        macro = MacroRate(
            date=today, risk_free_rate=4.25, market_return=10.0,
            inflation_rate=3.0, equity_risk_premium=5.75, fed_funds_rate=5.25,
        )
        _upsert_macro(session, macro)
        result["status"] = "fallback"
        result["error"] = str(e)

    return result


def _upsert_macro(session: Session, macro: MacroRate):
    """Insert or update macro rate for the given date."""
    existing = session.query(MacroRate).filter(MacroRate.date == macro.date).first()
    if existing:
        existing.risk_free_rate = macro.risk_free_rate
        existing.market_return = macro.market_return
        existing.inflation_rate = macro.inflation_rate
        existing.equity_risk_premium = macro.equity_risk_premium
        existing.fed_funds_rate = macro.fed_funds_rate
    else:
        session.add(macro)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A.2: FMP API — Quarterly Financial Statements
# ══════════════════════════════════════════════════════════════════════════════

def etl_financials_fmp(session: Session, ticker: str, company: Company) -> dict:
    """
    Pull quarterly financial statements from Financial Modeling Prep:
        - Income Statement (quarterly, last 8 quarters)
        - Balance Sheet (quarterly, last 8 quarters)
        - Cash Flow Statement (quarterly, last 8 quarters)

    Merges all three into the QuarterlyFinancials table.
    """
    result = {"status": "ok", "ticker": ticker, "quarters_loaded": 0}

    if not FMP_API_KEY:
        print(f"  [{ticker}] ⚠ No FMP key — falling back to yfinance fundamentals.")
        return etl_financials_yfinance_fallback(session, ticker, company)

    try:
        # ── Income Statement ──
        income_url = f"{FMP_BASE_URL}/income-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        income_data = _fmp_get(income_url)
        time.sleep(FMP_DELAY)

        # ── Balance Sheet ──
        bs_url = f"{FMP_BASE_URL}/balance-sheet-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        bs_data = _fmp_get(bs_url)
        time.sleep(FMP_DELAY)

        # ── Cash Flow ──
        cf_url = f"{FMP_BASE_URL}/cash-flow-statement/{ticker}?period=quarter&limit=8&apikey={FMP_API_KEY}"
        cf_data = _fmp_get(cf_url)
        time.sleep(FMP_DELAY)

        if not income_data:
            print(f"  [{ticker}] ⚠ FMP returned no income data — trying yfinance fallback.")
            return etl_financials_yfinance_fallback(session, ticker, company)

        # ── Merge by period ──
        # Index balance sheet and cash flow by period date
        bs_map = {item.get("date", ""): item for item in (bs_data or [])}
        cf_map = {item.get("date", ""): item for item in (cf_data or [])}

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

            # Check if this quarter already exists
            existing = session.query(QuarterlyFinancials).filter(
                QuarterlyFinancials.company_id == company.id,
                QuarterlyFinancials.period_end == period_date,
            ).first()

            if existing and existing.data_source == "fmp":
                continue  # Skip if already loaded from FMP

            qf = existing or QuarterlyFinancials(company_id=company.id, period_end=period_date)

            # Income Statement
            qf.revenue            = _safe(inc.get("revenue"))
            qf.cost_of_revenue    = _safe(inc.get("costOfRevenue"))
            qf.gross_profit       = _safe(inc.get("grossProfit"))
            qf.operating_income   = _safe(inc.get("operatingIncome"))
            qf.ebit               = _safe(inc.get("operatingIncome"))  # EBIT ≈ operating income
            qf.ebitda             = _safe(inc.get("ebitda"))
            qf.net_income         = _safe(inc.get("netIncome"))
            qf.interest_expense   = _safe(inc.get("interestExpense"))
            qf.income_tax_expense = _safe(inc.get("incomeTaxExpense"))

            # Balance Sheet
            qf.total_assets        = _safe(bs.get("totalAssets"))
            qf.total_liabilities   = _safe(bs.get("totalLiabilities"))
            qf.current_assets      = _safe(bs.get("totalCurrentAssets"))
            qf.current_liabilities = _safe(bs.get("totalCurrentLiabilities"))
            qf.total_equity        = _safe(bs.get("totalStockholdersEquity"))
            qf.retained_earnings   = _safe(bs.get("retainedEarnings"))
            qf.total_debt          = _safe(bs.get("totalDebt")) or _safe(bs.get("longTermDebt"))
            qf.cash_and_equivalents = _safe(bs.get("cashAndCashEquivalents"))

            # Cash Flow
            qf.operating_cash_flow = _safe(cf.get("operatingCashFlow"))
            qf.capital_expenditure = _safe(cf.get("capitalExpenditure"))
            qf.free_cash_flow      = _safe(cf.get("freeCashFlow"))
            qf.depreciation        = _safe(cf.get("depreciationAndAmortization"))

            # Meta
            qf.fiscal_year    = inc.get("calendarYear")
            qf.fiscal_quarter = _parse_quarter(inc.get("period", ""))
            qf.data_source    = "fmp"

            if not existing:
                session.add(qf)

            result["quarters_loaded"] += 1

        print(f"  [{ticker}] ✓ FMP: {result['quarters_loaded']} quarters loaded.")

    except Exception as e:
        print(f"  [{ticker}] ✗ FMP Error: {e}")
        result["status"] = "error"
        result["error"] = str(e)
        # Try yfinance fallback
        return etl_financials_yfinance_fallback(session, ticker, company)

    return result


def _fmp_get(url: str) -> list:
    """Make a GET request to FMP API with error handling."""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "Error Message" in data:
            print(f"    FMP Error: {data['Error Message']}")
            return []
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"    FMP request failed: {e}")
        return []


def _parse_quarter(period_str: str) -> Optional[int]:
    """Parse FMP period string (e.g. 'Q1', 'Q2') to integer."""
    if not period_str:
        return None
    period_str = period_str.upper().strip()
    for q in [1, 2, 3, 4]:
        if f"Q{q}" in period_str:
            return q
    return None


# ── yfinance Fallback for Fundamentals ─────────────────────────────────────────

def etl_financials_yfinance_fallback(session: Session, ticker: str, company: Company) -> dict:
    """
    Fallback: Use yfinance to extract basic financial data.
    Less comprehensive than FMP but works without an API key.
    """
    result = {"status": "ok", "ticker": ticker, "quarters_loaded": 0, "source": "yfinance"}

    try:
        t = yf.Ticker(ticker)

        # Quarterly financials from yfinance
        try:
            inc_q = t.quarterly_income_stmt
        except Exception:
            inc_q = pd.DataFrame()

        try:
            bs_q = t.quarterly_balance_sheet
        except Exception:
            bs_q = pd.DataFrame()

        try:
            cf_q = t.quarterly_cashflow
        except Exception:
            cf_q = pd.DataFrame()

        if inc_q.empty:
            print(f"  [{ticker}] ⚠ yfinance returned no quarterly data.")
            return result

        for col_date in inc_q.columns[:8]:  # Up to 8 quarters
            try:
                if hasattr(col_date, "date"):
                    period_date = col_date.date()
                else:
                    period_date = pd.to_datetime(col_date).date()
            except Exception:
                continue

            # Check existing
            existing = session.query(QuarterlyFinancials).filter(
                QuarterlyFinancials.company_id == company.id,
                QuarterlyFinancials.period_end == period_date,
            ).first()

            if existing:
                continue

            def yf_val(df, *keys):
                """Extract value from yfinance DataFrame, trying multiple row names."""
                if df.empty or col_date not in df.columns:
                    return None
                for key in keys:
                    if key in df.index:
                        v = df.loc[key, col_date]
                        return _safe(v)
                return None

            qf = QuarterlyFinancials(
                company_id=company.id,
                period_end=period_date,
                revenue=yf_val(inc_q, "Total Revenue", "Revenue"),
                cost_of_revenue=yf_val(inc_q, "Cost Of Revenue"),
                gross_profit=yf_val(inc_q, "Gross Profit"),
                operating_income=yf_val(inc_q, "Operating Income", "EBIT"),
                ebit=yf_val(inc_q, "EBIT", "Operating Income"),
                ebitda=yf_val(inc_q, "EBITDA", "Normalized EBITDA"),
                net_income=yf_val(inc_q, "Net Income", "Net Income Common Stockholders"),
                interest_expense=yf_val(inc_q, "Interest Expense", "Interest Expense Non Operating"),
                income_tax_expense=yf_val(inc_q, "Tax Provision", "Income Tax Expense"),
                total_assets=yf_val(bs_q, "Total Assets"),
                total_liabilities=yf_val(bs_q, "Total Liabilities Net Minority Interest", "Total Liabilities"),
                current_assets=yf_val(bs_q, "Current Assets", "Total Current Assets"),
                current_liabilities=yf_val(bs_q, "Current Liabilities", "Total Current Liabilities"),
                total_equity=yf_val(bs_q, "Stockholders Equity", "Total Stockholders Equity", "Total Equity Gross Minority Interest"),
                retained_earnings=yf_val(bs_q, "Retained Earnings"),
                total_debt=yf_val(bs_q, "Total Debt", "Long Term Debt"),
                cash_and_equivalents=yf_val(bs_q, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
                operating_cash_flow=yf_val(cf_q, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
                capital_expenditure=yf_val(cf_q, "Capital Expenditure"),
                free_cash_flow=yf_val(cf_q, "Free Cash Flow"),
                depreciation=yf_val(cf_q, "Depreciation And Amortization"),
                data_source="yfinance",
            )
            session.add(qf)
            result["quarters_loaded"] += 1

        print(f"  [{ticker}] ✓ yfinance fallback: {result['quarters_loaded']} quarters loaded.")

    except Exception as e:
        print(f"  [{ticker}] ✗ yfinance fallback error: {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A.3: yfinance — Daily Prices + Company Metadata
# ══════════════════════════════════════════════════════════════════════════════

def etl_daily_prices(session: Session, ticker: str, company: Company, period: str = "2y") -> dict:
    """
    Pull daily OHLCV data from yfinance and store in DailyPrice table.
    Also updates Company metadata (shares outstanding, beta, market cap, etc.).
    """
    result = {"status": "ok", "ticker": ticker, "days_loaded": 0}

    try:
        t = yf.Ticker(ticker)

        # ── Company metadata update ──
        info = t.info or {}
        company.name = info.get("longName") or info.get("shortName") or company.name
        company.sector = info.get("sector") or company.sector
        company.industry = info.get("industry") or company.industry
        company.country = info.get("country") or company.country
        company.exchange = info.get("exchange") or company.exchange
        company.currency = info.get("currency") or company.currency or "USD"
        company.website = info.get("website") or company.website
        company.description = info.get("longBusinessSummary") or company.description
        company.shares_outstanding = _safe(info.get("sharesOutstanding")) or company.shares_outstanding
        company.market_cap = _safe(info.get("marketCap")) or company.market_cap
        company.beta = _safe(info.get("beta")) or company.beta
        company.employees = info.get("fullTimeEmployees") or company.employees

        # ── Daily price history ──
        df = t.history(period=period, auto_adjust=True)
        if df.empty:
            print(f"  [{ticker}] ⚠ No price history returned.")
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

            # Check existing
            existing = session.query(DailyPrice).filter(
                DailyPrice.company_id == company.id,
                DailyPrice.date == price_date,
            ).first()

            if existing:
                # Update close price in case of adjustments
                existing.close = _safe(row.get("close"))
                existing.volume = int(row["volume"]) if pd.notna(row.get("volume")) else None
                continue

            dp = DailyPrice(
                company_id=company.id,
                date=price_date,
                open=_safe(row.get("open")),
                high=_safe(row.get("high")),
                low=_safe(row.get("low")),
                close=_safe(row.get("close")),
                volume=int(row["volume"]) if pd.notna(row.get("volume")) else None,
                adj_close=_safe(row.get("close")),
            )
            session.add(dp)
            result["days_loaded"] += 1

        print(f"  [{ticker}] ✓ Prices: {result['days_loaded']} new days loaded (total ~{len(df)} in period).")

    except Exception as e:
        print(f"  [{ticker}] ✗ Price ETL error: {e}")
        result["status"] = "error"
        result["error"] = str(e)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# MASTER ETL ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def run_nightly_etl(tickers: Optional[list[str]] = None) -> dict:
    """
    Phase A: Complete Nightly ETL Pipeline.
    1. Pull macro rates from FRED
    2. For each ticker: FMP financials → yfinance prices
    3. Commit all to database

    Args:
        tickers: Optional list of tickers. If None, processes all tracked companies.
    """
    init_quant_db()  # Ensure schema exists
    session = get_session()
    results = {"macro": {}, "companies": [], "errors": []}

    try:
        # ── Step 1: Macro Rates ──
        print("\n" + "=" * 60)
        print("  NIGHTLY ETL — Phase A: Data Extraction")
        print("=" * 60)
        print("\n  [1/3] Fetching Macro Rates from FRED...")
        results["macro"] = etl_macro_rates(session)
        session.commit()

        # ── Step 2: Determine ticker universe ──
        if tickers:
            ticker_list = [t.upper().strip() for t in tickers]
        else:
            # Get all active companies from quant DB
            companies = session.query(Company).filter(Company.is_active == True).all()
            ticker_list = [c.ticker for c in companies]

            # Also check legacy tracked_stocks table for any tickers not yet in Companies
            try:
                import database as legacy_db
                legacy_stocks = legacy_db.get_tracked_stocks()
                for ls in legacy_stocks:
                    if ls["ticker"] not in ticker_list:
                        ticker_list.append(ls["ticker"])
            except Exception:
                pass

        if not ticker_list:
            print("  ⚠ No tickers to process. Add stocks to your watchlist first.")
            return results

        print(f"\n  [2/3] Processing {len(ticker_list)} tickers: {', '.join(ticker_list)}")

        # ── Step 3: ETL each ticker ──
        for i, ticker in enumerate(ticker_list, 1):
            print(f"\n  ── [{i}/{len(ticker_list)}] {ticker} {'─' * (40 - len(ticker))}")
            company_result = {"ticker": ticker}

            try:
                # Get or create Company record
                company = get_or_create_company(session, ticker)
                session.flush()

                # Financials (FMP with yfinance fallback)
                print(f"  [{ticker}] Fetching quarterly financials...")
                fin_result = etl_financials_fmp(session, ticker, company)
                company_result["financials"] = fin_result
                session.flush()

                # Daily prices + metadata
                print(f"  [{ticker}] Fetching daily prices...")
                price_result = etl_daily_prices(session, ticker, company, period="2y")
                company_result["prices"] = price_result
                session.flush()

                session.commit()  # Commit after each ticker for resilience
                results["companies"].append(company_result)

            except Exception as e:
                session.rollback()
                print(f"  [{ticker}] ✗ FATAL: {e}")
                traceback.print_exc()
                results["errors"].append({"ticker": ticker, "error": str(e)})

            # Respect API rate limits
            time.sleep(0.3)

        print(f"\n{'=' * 60}")
        print(f"  ETL COMPLETE: {len(results['companies'])} tickers processed, "
              f"{len(results['errors'])} errors")
        print(f"{'=' * 60}\n")

    except Exception as e:
        session.rollback()
        print(f"  FATAL ETL ERROR: {e}")
        traceback.print_exc()
        raise
    finally:
        session.close()

    return results


# ── Module Self-Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    tickers = sys.argv[1:] if len(sys.argv) > 1 else None
    if tickers:
        print(f"Running ETL for: {tickers}")
    else:
        print("Running ETL for all tracked tickers...")

    result = run_nightly_etl(tickers)
    print(f"\nETL Results Summary:")
    print(f"  Macro: {result['macro']}")
    print(f"  Companies: {len(result['companies'])}")
    print(f"  Errors: {len(result['errors'])}")
