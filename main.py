"""
main.py — FastAPI Backend for Tőzsde Figyelő (Stock Investment Intelligence)
Run with: python -m uvicorn main:app --reload --port 8000
"""

import sys
import io
import traceback
from typing import Optional

# Force UTF-8 encoding for stdout/stderr to prevent Windows charmap crashes when printing Unicode symbols
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from fastapi import FastAPI, HTTPException, Query, Body, Depends, BackgroundTasks
from auth import router as auth_router, get_current_user
from models import User
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List as TList
import os
import math
import re
import threading
import ast

import database as db
import data_factory as yfc

# Quant engine imports
from models import (
    init_quant_db, get_session, Company, QuarterlyFinancials,
    DailyPrice, Valuation, MacroRate, DataProvider, MathFormula,
    get_or_create_company, get_latest_macro, get_latest_valuation,
    CustomObject, CustomField, CustomRecord,
)
from valuation_engine import run_all_valuations, run_valuation_for_company, run_scenario_sweep
from etl_pipeline import run_nightly_etl
from intraday_monitor import check_intraday_prices
from screener import QuantitativeScreener
from math_config import get_config
from async_etl import run_async_etl_sync

# ── App Setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Tőzsde Figyelő API",
    description="Investment Intelligence Platform — Real-time stock data, technical analysis & portfolio optimization",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
@app.on_event("startup")
async def startup():
    db.init_db()
    init_quant_db()  # Initialize SQLAlchemy quant tables

# Serve frontend static files
BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")
app.include_router(auth_router)

import api_paas
app.include_router(api_paas.router)

@app.get("/", include_in_schema=False)
def serve_frontend_root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/index.html", include_in_schema=False)
def serve_frontend_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/admin.html", include_in_schema=False)
def serve_admin():
    return FileResponse(os.path.join(BASE_DIR, "admin.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ── Request / Response Models ─────────────────────────────────────────────────

class AddStockRequest(BaseModel):
    ticker: str
    period: Optional[str] = "1yr"  # How much history to load initially


class CompareRequest(BaseModel):
    tickers: list[str]


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check(current_user: User = Depends(get_current_user)):
    return {"status": "ok", "message": "Tőzsde Figyelő API is running"}


# ── Stock Tracking ────────────────────────────────────────────────────────────

@app.get("/api/stocks", summary="List all tracked stocks")
def list_stocks(current_user: User = Depends(get_current_user)):
    """Return all tracked stocks with their latest cached price data."""
    stocks = db.get_tracked_stocks(current_user.id)
    # Compute change % from cached data
    for s in stocks:
        price = s.get("current_price")
        prev  = s.get("previous_close")
        if price and prev and prev != 0:
            s["change"]         = round(price - prev, 4)
            s["change_percent"] = round((price - prev) / prev * 100, 2)
        else:
            s["change"]         = None
            s["change_percent"] = None
    return {"stocks": stocks, "count": len(stocks)}


@app.post("/api/stocks", summary="Add a stock to the watchlist")
def add_stock(req: AddStockRequest, current_user: User = Depends(get_current_user)):
    """
    Add a new ticker to tracking:
    1. Validate ticker via Yahoo Finance
    2. Store metadata & current price in DB
    3. Download historical OHLCV data and store it
    """
    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty.")

    if db.stock_exists(current_user.id, ticker):
        raise HTTPException(status_code=409, detail=f"'{ticker}' is already being tracked.")

    try:
        info = yfc.fetch_ticker_info(ticker)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for '{ticker}': {str(e)}")

    # Save metadata and cache
    db.add_tracked_stock(current_user.id, info["metadata"])
    db.upsert_stock_cache(ticker, info["cache"])

    # Download history
    try:
        history = yfc.fetch_history(ticker, period=req.period)
        if history:
            db.upsert_stock_history(ticker, history)
    except Exception as e:
        # Non-fatal — we still have the metadata
        print(f"Warning: Could not fetch history for {ticker}: {e}")

    return {
        "success": True,
        "ticker":  ticker,
        "name":    info["metadata"]["name"],
        "message": f"'{ticker}' added to watchlist successfully.",
    }


@app.delete("/api/stocks/{ticker}", summary="Remove a stock from the watchlist")
def remove_stock(ticker: str, current_user: User = Depends(get_current_user)):
    """Remove a stock ticker from tracking (cascades to history and cache)."""
    ticker = ticker.strip().upper()
    if not db.stock_exists(current_user.id, ticker):
        raise HTTPException(status_code=404, detail=f"'{ticker}' is not being tracked.")
    db.remove_tracked_stock(current_user.id, ticker)
    return {"success": True, "message": f"'{ticker}' removed from watchlist."}


@app.post("/api/stocks/refresh", summary="Refresh live prices for all tracked stocks")
def refresh_all(current_user: User = Depends(get_current_user)):
    """Fetch the latest prices for all tracked stocks from Yahoo Finance."""
    stocks = db.get_tracked_stocks(current_user.id)
    updated = []
    errors  = []

    for stock in stocks:
        ticker = stock["ticker"]
        try:
            cache = yfc.fetch_live_price(ticker)
            db.upsert_stock_cache(ticker, cache)
            updated.append(ticker)
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})

    return {
        "updated": updated,
        "errors":  errors,
        "message": f"Refreshed {len(updated)} stocks.",
    }


# ── History ───────────────────────────────────────────────────────────────────

PERIOD_DAYS = {"1mo": 30, "3mo": 90, "6mo": 180, "1yr": 365, "2yr": 730, "5yr": 1825, "max": 10000}

@app.get("/api/stocks/{ticker}/history", summary="Get OHLCV price history")
def get_history(
    ticker: str,
    period: str = Query("6mo", description="Period: 1mo, 3mo, 6mo, 1yr, 2yr, 5yr, max"),
    refresh: bool = Query(False, description="Force re-download from Yahoo Finance"),
    current_user: User = Depends(get_current_user)):
    """
    Return OHLCV price history from the local database.
    If 'refresh' is True or data is missing, fetches from Yahoo Finance.
    """
    ticker = ticker.strip().upper()
    if not db.stock_exists(current_user.id, ticker):
        raise HTTPException(status_code=404, detail=f"'{ticker}' is not being tracked.")

    limit = PERIOD_DAYS.get(period, 365)
    history = db.get_stock_history(ticker, limit=limit)

    # If not enough data or refresh requested, re-download
    if refresh or len(history) < 5:
        try:
            new_data = yfc.fetch_history(ticker, period=period)
            if new_data:
                db.upsert_stock_history(ticker, new_data)
                history = db.get_stock_history(ticker, limit=limit)
        except Exception as e:
            if not history:
                raise HTTPException(status_code=502, detail=f"Could not fetch history: {str(e)}")

    return {
        "ticker":  ticker,
        "period":  period,
        "count":   len(history),
        "history": history,
    }


# ── Company Info ──────────────────────────────────────────────────────────────

@app.get("/api/stocks/{ticker}/info", summary="Get detailed company info")
def get_stock_info(ticker: str, fresh: bool = Query(False), current_user: User = Depends(get_current_user)):
    """
    Return detailed company info including fundamentals.
    If fresh=True, re-fetches from Yahoo Finance (slower but up-to-date).
    """
    ticker = ticker.strip().upper()
    if not db.stock_exists(current_user.id, ticker):
        raise HTTPException(status_code=404, detail=f"'{ticker}' is not being tracked.")

    stocks = db.get_tracked_stocks(current_user.id)
    stock = next((s for s in stocks if s["ticker"] == ticker), None)

    if fresh or not stock:
        try:
            info = yfc.fetch_ticker_info(ticker)
            db.add_tracked_stock(current_user.id, info["metadata"])
            db.upsert_stock_cache(ticker, info["cache"])
            stocks = db.get_tracked_stocks(current_user.id)
            stock = next((s for s in stocks if s["ticker"] == ticker), {})
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    # Add derived fields
    if stock.get("current_price") and stock.get("previous_close"):
        p, pc = stock["current_price"], stock["previous_close"]
        stock["change"]         = round(p - pc, 4)
        stock["change_percent"] = round((p - pc) / pc * 100, 2) if pc else None

    return stock


# ── Technical Analysis ────────────────────────────────────────────────────────

@app.get("/api/analysis/{ticker}", summary="Technical indicators & investment signals")
def get_analysis(
    ticker: str,
    period: str = Query("1yr", description="History period for analysis: 6mo, 1yr, 2yr"),
    current_user: User = Depends(get_current_user)):
    """
    Compute technical indicators and multi-strategy investment signals.
    Returns: SMA20/50, EMA20, RSI, Bollinger Bands, MACD + strategy signals.
    Auto-downloads extended history if the stored data is insufficient.
    """
    ticker = ticker.strip().upper()
    if not db.stock_exists(current_user.id, ticker):
        raise HTTPException(status_code=404, detail=f"'{ticker}' is not being tracked.")

    limit = PERIOD_DAYS.get(period, 365)
    history = db.get_stock_history(ticker, limit=limit)

    # Auto-extend history if we don't have enough data for the requested period
    if len(history) < 50:
        try:
            new_data = yfc.fetch_history(ticker, period=period)
            if new_data:
                db.upsert_stock_history(ticker, new_data)
                history = db.get_stock_history(ticker, limit=limit)
        except Exception as e:
            if len(history) < 20:
                raise HTTPException(status_code=502, detail=str(e))

    if len(history) < 20:
        raise HTTPException(status_code=422, detail="Not enough history data for analysis (need 20+ days).")

    indicators = yfc.compute_indicators(history)
    signals    = yfc.compute_signals(history)

    # If signals returned an error (e.g. not enough data), raise 422
    if isinstance(signals, dict) and "error" in signals:
        raise HTTPException(status_code=422, detail=signals["error"])

    return {
        "ticker":     ticker,
        "period":     period,
        "indicators": indicators,
        "signals":    signals,
    }


# ── Stock Comparison ──────────────────────────────────────────────────────────

@app.get("/api/compare", summary="Compare multiple stocks normalized performance")
def compare_stocks(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,TSLA"),
    period:  str = Query("6mo"),
    current_user: User = Depends(get_current_user)):
    """
    Compare the normalized price performance of multiple stocks.
    Returns percentage change from the start of the selected period.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 tickers to compare.")

    limit = PERIOD_DAYS.get(period, 180)
    result = {}

    for ticker in ticker_list:
        if not db.stock_exists(current_user.id, ticker):
            # Auto-add if not tracked
            try:
                info = yfc.fetch_ticker_info(ticker)
                db.add_tracked_stock(current_user.id, info["metadata"])
                db.upsert_stock_cache(ticker, info["cache"])
                hist = yfc.fetch_history(ticker, period=period)
                if hist:
                    db.upsert_stock_history(ticker, hist)
            except Exception as e:
                result[ticker] = {"error": str(e)}
                continue

        history = db.get_stock_history(ticker, limit=limit)
        if not history:
            result[ticker] = {"error": "No history available"}
            continue

        closes = [h["close"] for h in history if h["close"] is not None]
        dates  = [h["date"]  for h in history if h["close"] is not None]
        if not closes:
            result[ticker] = {"error": "No close price data"}
            continue

        base = closes[0]
        normalized = [round((c - base) / base * 100, 4) for c in closes]

        result[ticker] = {
            "dates":      dates,
            "closes":     closes,
            "normalized": normalized,
            "total_return": normalized[-1] if normalized else None,
        }

    return {"period": period, "comparison": result}


# ── Portfolio Optimization ────────────────────────────────────────────────────

@app.get("/api/portfolio/optimize", summary="Markowitz portfolio optimization")
def optimize_portfolio(
    tickers: str = Query(..., description="Comma-separated tickers to optimize"),
    period:  str = Query("2yr", description="History period for return computation"),
    current_user: User = Depends(get_current_user)):
    """
    Run Monte Carlo portfolio optimization (3000 simulations).
    Returns the efficient frontier, optimal Sharpe-ratio portfolio,
    and minimum-volatility portfolio with recommended weights.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 tickers to optimize.")

    limit = PERIOD_DAYS.get(period, 730)
    histories = {}

    for ticker in ticker_list:
        has_stock = db.stock_exists(current_user.id, ticker)
        has_history = db.get_stock_history(ticker, limit=1)
        
        if not has_stock or not has_history:
            try:
                if not has_stock:
                    info = yfc.fetch_ticker_info(ticker)
                    db.add_tracked_stock(current_user.id, info["metadata"])
                    db.upsert_stock_cache(ticker, info["cache"])
                hist = yfc.fetch_history(ticker, period=period)
                if hist:
                    db.upsert_stock_history(ticker, hist)
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Could not fetch '{ticker}': {str(e)}")

        history = db.get_stock_history(ticker, limit=limit)
        if history:
            histories[ticker] = history

    if len(histories) < 2:
        raise HTTPException(status_code=422, detail="Not enough data for at least 2 tickers.")

    result = yfc.compute_portfolio_optimization(histories)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return result


# ══════════════════════════════════════════════════════════════════════════════
# QUANT ENGINE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Valuations ────────────────────────────────────────────────────────────────

@app.get("/api/valuations", summary="List all stocks with valuation data")
def list_valuations(current_user: User = Depends(get_current_user)):
    """Return all tracked stocks with their latest computed valuation (DCF, Z-score, signal)."""
    session = get_session()
    try:
        companies = session.query(Company).filter(Company.is_active == True).all()
        result = []
        for c in companies:
            val = get_latest_valuation(session, c.id)
            entry = c.to_dict()
            entry["valuation"] = val.to_dict() if val else None
            result.append(entry)
        return {"valuations": result, "count": len(result)}
    finally:
        session.close()


@app.get("/api/valuations/{ticker}", summary="Detailed valuation for a single stock")
def get_valuation(ticker: str, current_user: User = Depends(get_current_user)):
    """Return full DCF/CAPM/Z-score breakdown for a specific stock."""
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        company = session.query(Company).filter(Company.ticker == ticker).first()
        if not company:
            raise HTTPException(status_code=404, detail=f"'{ticker}' not found in quant database.")

        val = get_latest_valuation(session, company.id)
        if not val:
            raise HTTPException(status_code=404, detail=f"No valuation computed for '{ticker}'. Run the valuation engine first.")

        # Get recent quarterly financials for context
        financials = session.query(QuarterlyFinancials).filter(
            QuarterlyFinancials.company_id == company.id
        ).order_by(QuarterlyFinancials.period_end.desc()).limit(8).all()

        return {
            "company": company.to_dict(),
            "valuation": val.to_dict(),
            "quarterly_financials": [f.to_dict() for f in financials],
        }
    finally:
        session.close()


@app.get("/api/valuations/{ticker}/dcf-breakdown", summary="Step-by-step DCF components")
def get_dcf_breakdown(ticker: str, current_user: User = Depends(get_current_user)):
    """Return the detailed DCF calculation breakdown for audit and transparency."""
    ticker = ticker.strip().upper()
    session = get_session()
    try:
        company = session.query(Company).filter(Company.ticker == ticker).first()
        if not company:
            raise HTTPException(status_code=404, detail=f"'{ticker}' not found.")

        val = get_latest_valuation(session, company.id)
        if not val:
            raise HTTPException(status_code=404, detail=f"No valuation for '{ticker}'.")

        return {
            "ticker": ticker,
            "name": company.name,
            "dcf": {
                "intrinsic_value_per_share": val.intrinsic_value_dcf,
                "market_price": val.market_price,
                "margin_of_safety_pct": val.margin_of_safety_pct,
                "fcf_growth_rate": val.fcf_growth_rate,
                "terminal_growth_rate": val.terminal_growth_rate,
                "wacc": val.wacc,
                "pv_of_projected_fcfs": val.pv_of_fcfs,
                "terminal_value": val.terminal_value,
                "pv_of_terminal_value": val.pv_of_terminal,
                "enterprise_value": val.enterprise_value,
                "net_debt": val.net_debt,
                "equity_value": val.equity_value,
            },
            "capm": {
                "expected_return": val.capm_expected_return,
                "risk_free_rate": val.risk_free_rate,
                "equity_risk_premium": val.equity_risk_premium,
                "beta": val.beta_used,
                "cost_of_equity": val.cost_of_equity,
                "cost_of_debt": val.cost_of_debt,
            },
            "altman_z": {
                "z_score": val.altman_z_score,
                "zone": val.z_score_zone,
                "x1_wc_ta": val.z_x1,
                "x2_re_ta": val.z_x2,
                "x3_ebit_ta": val.z_x3,
                "x4_mcap_tl": val.z_x4,
                "x5_rev_ta": val.z_x5,
            },
            "signal": val.signal,
            "signal_reason": val.signal_reason,
            "data_quality": val.data_quality,
            "computed_at": val.computed_at.isoformat() if val.computed_at else None,
        }
    finally:
        session.close()


# ── Run Engines ───────────────────────────────────────────────────────────────

@app.post("/api/valuations/run", summary="Run valuation engine (Phase B)")
def trigger_valuations():
    """Run the valuation engine for all active companies. Computes DCF, CAPM, Z-score, and signals."""
    try:
        result = run_all_valuations()
        return {
            "success": True,
            "message": f"Valuation engine complete: {len(result['computed'])} stocks processed.",
            "results": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Valuation engine error: {str(e)}")


@app.post("/api/etl/run", summary="Run nightly ETL pipeline (Phase A)")
def trigger_etl(
    tickers: Optional[str] = Query(None, description="Comma-separated tickers, or omit for all")
):
    """Run the full ETL pipeline: FRED macro rates → FMP financials → yfinance prices."""
    ticker_list = None
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    try:
        result = run_nightly_etl(ticker_list)
        return {
            "success": True,
            "message": f"ETL complete: {len(result['companies'])} companies processed.",
            "results": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ETL error: {str(e)}")


# ── Macro Rates ───────────────────────────────────────────────────────────────

@app.get("/api/macro", summary="Current macroeconomic rates")
def get_macro_rates(current_user: User = Depends(get_current_user)):
    """Return the latest FRED macro rates (risk-free rate, market return, inflation, ERP)."""
    session = get_session()
    try:
        macro = get_latest_macro(session)
        if not macro:
            return {"macro": None, "message": "No macro rates loaded. Run ETL first."}
        return {"macro": macro.to_dict()}
    finally:
        session.close()


# ── Active Signals ────────────────────────────────────────────────────────────

@app.get("/api/signals", summary="All active buy/sell/value-trap signals")
def get_all_signals(current_user: User = Depends(get_current_user)):
    """Return all current signals from the valuation engine, sorted by strength."""
    session = get_session()
    try:
        companies = session.query(Company).filter(Company.is_active == True).all()
        signals = []
        for c in companies:
            val = get_latest_valuation(session, c.id)
            if val and val.signal:
                signals.append({
                    "ticker": c.ticker,
                    "name": c.name,
                    "sector": c.sector,
                    "signal": val.signal,
                    "signal_reason": val.signal_reason,
                    "margin_of_safety_pct": val.margin_of_safety_pct,
                    "intrinsic_value": val.intrinsic_value_dcf,
                    "market_price": val.market_price,
                    "altman_z_score": val.altman_z_score,
                    "z_score_zone": val.z_score_zone,
                    "data_quality": val.data_quality,
                    "computed_at": val.computed_at.isoformat() if val.computed_at else None,
                })

        # Sort: STRONG_BUY first, then BUY, then HOLD, then VALUE_TRAP, then OVERVALUED
        signal_order = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "VALUE_TRAP": 3, "OVERVALUED": 4, "INSUFFICIENT_DATA": 5}
        signals.sort(key=lambda s: signal_order.get(s["signal"], 9))

        return {"signals": signals, "count": len(signals)}
    finally:
        session.close()


# ── Intraday Monitor (single check) ──────────────────────────────────────────

@app.get("/api/monitor/check", summary="Run a single intraday price check")
def run_monitor_check(current_user: User = Depends(get_current_user)):
    """Run a single intraday price check and return any alerts."""
    try:
        alerts = check_intraday_prices()
        opportunities = [a for a in alerts if a.get("alert_type") == "OPPORTUNITY"]
        traps = [a for a in alerts if a.get("alert_type") == "VALUE_TRAP_WARNING"]
        return {
            "alerts": alerts,
            "opportunities": len(opportunities),
            "value_traps": len(traps),
            "total_checked": len(alerts),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Monitor error: {str(e)}")


# ── Screener (Phase 2: Dynamic Segmentation) ─────────────────────────────────

@app.get("/api/screener", summary="Dynamic quantitative screener")
def run_screener(
    sector: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    signal: Optional[str] = Query(None),
    z_zone: Optional[str] = Query(None),
    min_mos: Optional[float] = Query(None),
    max_mos: Optional[float] = Query(None),
    min_z: Optional[float] = Query(None),
    max_z: Optional[float] = Query(None),
    min_wacc: Optional[float] = Query(None),
    max_wacc: Optional[float] = Query(None),
    max_dte: Optional[float] = Query(None),
    min_fcf_yield: Optional[float] = Query(None),
    sort_by: str = Query("margin_of_safety_pct"),
    descending: bool = Query(True),
    limit: int = Query(100),
    offset: int = Query(0),
    top_decile: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user)):
    """Dynamic multi-parameter screener across all tracked equities."""
    params = {
        k: v for k, v in {
            "sector": sector, "industry": industry, "signal": signal,
            "z_zone": z_zone, "min_mos": min_mos, "max_mos": max_mos,
            "min_z": min_z, "max_z": max_z, "min_wacc": min_wacc,
            "max_wacc": max_wacc, "max_dte": max_dte, "min_fcf_yield": min_fcf_yield,
            "sort_by": sort_by, "descending": descending, "limit": limit,
            "offset": offset, "top_decile": top_decile,
        }.items() if v is not None
    }
    try:
        results = QuantitativeScreener.from_params(params)
        return {"results": results, "count": len(results), "params": params}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screener error: {str(e)}")


# ── Async ETL (Phase 2: High-Performance) ─────────────────────────────────────

@app.post("/api/etl/async", summary="Run high-performance async ETL")
def trigger_async_etl(
    tickers: Optional[str] = Query(None, description="Comma-separated tickers, or omit for all"),
    current_user: User = Depends(get_current_user)):
    """Run the async ETL pipeline with concurrent API calls and bulk DB writes."""
    ticker_list = None
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    try:
        result = run_async_etl_sync(ticker_list)
        return {
            "success": True,
            "message": f"Async ETL complete: {len(result.get('tickers', []))} tickers processed.",
            "stats": result.get("stats", {}),
            "errors": result.get("errors", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Async ETL error: {str(e)}")


# ── Scenario Sweep (Phase 2: Bear/Base/Bull) ──────────────────────────────────

@app.post("/api/scenarios/sweep", summary="Run Bear/Base/Bull scenario sweep")
def trigger_scenario_sweep(
    scenarios: Optional[str] = Query(None, description="Comma-separated scenario names (e.g. bear,base,bull)"),
    current_user: User = Depends(get_current_user)):
    """Run valuations under multiple scenarios across all tracked stocks."""
    scenario_list = None
    if scenarios:
        scenario_list = [s.strip().lower() for s in scenarios.split(",") if s.strip()]
    try:
        result = run_scenario_sweep(scenario_list)
        return {
            "success": True,
            "scenarios": {k: {"count": len(v), "results": v} for k, v in result.items()},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scenario sweep error: {str(e)}")

def async_scenario_task(scenario_names: list):
    import time
    print(f"Async Scenario Sweep Started for {scenario_names}...")
    run_scenario_sweep(scenario_names)
    print("Async Scenario Sweep Completed successfully!")

@app.post("/api/valuations/scenarios/async", summary="Async Bear/Base/Bull scenario sweep")
def trigger_async_scenario_sweep(
    background_tasks: BackgroundTasks,
    scenarios: Optional[str] = Query(None, description="Comma-separated scenario names (e.g. bear,base,bull)"),
    current_user: User = Depends(get_current_user)):
    scenario_list = [s.strip().lower() for s in scenarios.split(",") if s.strip()] if scenarios else None
    background_tasks.add_task(async_scenario_task, scenario_list)
    return {
        "status": "Accepted",
        "message": "Scenario sweep started in the background.",
        "scenarios": scenario_list
    }


# ── Config API (Phase 2: Mathematical Transparency) ──────────────────────────

@app.get("/api/config", summary="View current mathematical configuration")
def get_config_api(current_user: User = Depends(get_current_user)):
    """Return all exposed financial assumptions, sector overrides, and scenario definitions."""
    cfg = get_config()
    return {
        "config": cfg.to_dict(),
        "scenarios": {
            name: {
                "label": cfg.scenario(name).label,
                "erp": cfg.scenario(name).equity_risk_premium,
                "terminal_growth": cfg.scenario(name).terminal_growth_rate,
                "fcf_growth_multiplier": cfg.scenario(name).fcf_growth_multiplier,
                "wacc_adjustment": cfg.scenario(name).wacc_adjustment,
                "mos_strong_buy": cfg.scenario(name).mos_strong_buy,
                "mos_buy": cfg.scenario(name).mos_buy,
            }
            for name in cfg.scenario_names()
        },
    }


@app.post("/api/config/reload", summary="Hot-reload config.yaml")
def reload_config(current_user: User = Depends(get_current_user)):
    """Hot-reload config.yaml from disk without restarting the server."""
    cfg = get_config()
    cfg.reload()
    return {"success": True, "message": "Configuration reloaded from config.yaml.", "config": cfg.to_dict()}


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: RECORDS, BULK IMPORT, ADMIN PLATFORM
# ══════════════════════════════════════════════════════════════════════════════

# ── Pydantic Models ───────────────────────────────────────────────────────────

class BulkImportRequest(BaseModel):
    tickers: TList[str]
    source: str = "manual"

class CustomObjectCreate(BaseModel):
    name: str
    label: str
    plural_label: str
    description: Optional[str] = None

class CustomObjectUpdate(BaseModel):
    label: Optional[str] = None
    plural_label: Optional[str] = None
    description: Optional[str] = None

class CustomFieldCreate(BaseModel):
    object_id: Optional[int] = None
    name: str
    label: str
    field_type: str = "text"
    formula: Optional[str] = None
    lookup_object: Optional[str] = None
    description: Optional[str] = None
    format_decimals: int = 2
    display_order: int = 0

class CustomFieldUpdate(BaseModel):
    label: Optional[str] = None
    field_type: Optional[str] = None
    formula: Optional[str] = None
    lookup_object: Optional[str] = None
    description: Optional[str] = None
    format_decimals: Optional[int] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None

class CustomRecordCreate(BaseModel):
    name: str
    data: dict

class CustomRecordUpdate(BaseModel):
    name: Optional[str] = None
    data: Optional[dict] = None

class PageLayoutCreate(BaseModel):
    object_id: int # -1 for Stock
    name: str
    layout_data: dict = {}

class PageLayoutUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    layout_data: Optional[dict] = None


# ── Safe Formula Evaluator ────────────────────────────────────────────────────

SAFE_MATH_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "pow": pow, "floor": math.floor, "ceil": math.ceil,
}

def evaluate_formula(formula: str, data: dict) -> Optional[float]:
    """Safely evaluate a formula against a data dict. Returns None on error."""
    try:
        safe_dict = {k: (v if v is not None else 0) for k, v in data.items() if isinstance(v, (int, float))}
        safe_dict.update(SAFE_MATH_NAMES)
        safe_dict["__builtins__"] = {}
        result = eval(formula, safe_dict)
        if isinstance(result, (int, float)) and not math.isnan(result) and not math.isinf(result):
            return float(result)
        return None
    except Exception:
        return None


def build_record_data(company, valuation, financials) -> dict:
    """Build a unified record dict from Company + latest Valuation + latest Financials."""
    d = company.to_dict() if company else {}
    if valuation:
        d.update({f"val_{k}": v for k, v in valuation.to_dict().items() if k not in ("id", "company_id")})
        # Also add flat names for formula evaluation
        d.update(valuation.to_dict())
    if financials:
        d.update({f"fin_{k}": v for k, v in financials.to_dict().items() if k not in ("id", "company_id")})
        d.update(financials.to_dict())
    return d


# ── Bulk Import ───────────────────────────────────────────────────────────────

# Stock index ticker lists for quick import
INDEX_TICKERS = {
    "sp500": None,   # Loaded dynamically from yfinance/Wikipedia
    "nasdaq100": None,
    "dow30": None,
}

def _fetch_index_tickers(index_name: str) -> list[str]:
    """Fetch index component tickers. Uses yfinance/pandas scraping."""
    import pandas as pd
    storage_opts = {'User-Agent': 'Mozilla/5.0'}
    try:
        if index_name == "sp500":
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            tables = pd.read_html(url, storage_options=storage_opts)
            return sorted(tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist())
        elif index_name == "nasdaq100":
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            tables = pd.read_html(url, storage_options=storage_opts)
            for t in tables:
                if "Ticker" in t.columns:
                    return sorted(t["Ticker"].str.replace(".", "-", regex=False).tolist())
                elif "Symbol" in t.columns:
                    return sorted(t["Symbol"].str.replace(".", "-", regex=False).tolist())
            return []
        elif index_name == "dow30":
            url = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"
            tables = pd.read_html(url, storage_options=storage_opts)
            for t in tables:
                if "Symbol" in t.columns:
                    return sorted(t["Symbol"].str.replace(".", "-", regex=False).tolist())
            return []
        elif index_name == "russell2000":
            # Russell 2000 is too large for Wikipedia; use a static fallback or yfinance
            return []
        else:
            return []
    except Exception as e:
        print(f"[bulk_import] Error fetching {index_name}: {e}")
        return []


def _bulk_import_worker(tickers: list[str], results: dict, user_id: int = 1):
    """Background worker that imports stocks via yfinance and adds to user watchlist."""
    import yfinance as yf
    session = get_session()
    imported = []
    errors = []
    skipped = []

    for i, ticker in enumerate(tickers):
        ticker = ticker.upper().strip()
        if not ticker or not re.match(r'^[A-Z0-9.\-]+$', ticker):
            errors.append({"ticker": ticker, "error": "Invalid ticker format"})
            continue

        # Check if already exists in Company table
        existing = session.query(Company).filter(Company.ticker == ticker).first()
        if existing:
            # Check if user already tracks it
            if not db.stock_exists(user_id, ticker):
                db.add_tracked_stock(user_id, {
                    "ticker": existing.ticker,
                    "name": existing.name,
                    "sector": existing.sector,
                    "industry": existing.industry,
                    "exchange": existing.exchange,
                    "country": existing.country,
                    "currency": existing.currency,
                    "website": existing.website,
                    "description": existing.description,
                    "market_cap": existing.market_cap,
                    "employees": existing.employees
                })
                imported.append(ticker)
            else:
                skipped.append(ticker)
            continue

        try:
            yf_ticker = yf.Ticker(ticker, session=yfc.custom_session)
            info = yf_ticker.info or {}

            if not info.get("shortName") and not info.get("longName"):
                errors.append({"ticker": ticker, "error": "Not found on Yahoo Finance"})
                continue

            company = Company(
                ticker=ticker,
                name=info.get("longName") or info.get("shortName") or ticker,
                sector=info.get("sector"),
                industry=info.get("industry"),
                country=info.get("country"),
                exchange=info.get("exchange"),
                currency=info.get("currency", "USD"),
                website=info.get("website"),
                description=info.get("longBusinessSummary"),
                shares_outstanding=info.get("sharesOutstanding"),
                market_cap=info.get("marketCap"),
                beta=info.get("beta"),
                employees=info.get("fullTimeEmployees"),
                is_active=True,
            )
            session.add(company)
            session.flush()

            # Also add to the legacy tracked_stocks for dashboard compatibility
            try:
                db.add_tracked_stock(user_id, {
                    "ticker": company.ticker,
                    "name": company.name,
                    "sector": company.sector,
                    "industry": company.industry,
                    "exchange": company.exchange,
                    "country": company.country,
                    "currency": company.currency,
                    "website": company.website,
                    "description": company.description,
                    "market_cap": company.market_cap,
                    "employees": company.employees
                })
            except Exception as e:
                pass

            imported.append(ticker)

            # Commit every 50 to avoid huge transactions
            if len(imported) % 50 == 0:
                session.commit()

        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)[:100]})

        # Update status dynamically
        results["imported"] = imported
        results["errors"] = errors
        results["skipped"] = skipped

    session.commit()
    session.close()

    results["done"] = True


# Global import status tracker
_import_status = {"done": True, "imported": [], "errors": [], "skipped": [], "total": 0}


@app.post("/api/stocks/bulk-import", summary="Bulk import stocks by ticker list")
def bulk_import_stocks(req: BulkImportRequest, current_user: User = Depends(get_current_user)):
    """Import up to 2000 stocks at once from a ticker list."""
    global _import_status
    tickers = [t.upper().strip() for t in req.tickers if t.strip()]

    if len(tickers) > 2000:
        raise HTTPException(400, "Maximum 2000 tickers per batch.")
    if len(tickers) == 0:
        raise HTTPException(400, "No valid tickers provided.")

    # Run synchronously for small batches, async for large
    if len(tickers) <= 50:
        results = {}
        _bulk_import_worker(tickers, results, current_user.id)
        return {
            "success": True,
            "imported": len(results.get("imported", [])),
            "skipped": len(results.get("skipped", [])),
            "errors": results.get("errors", []),
            "imported_tickers": results.get("imported", []),
            "skipped_tickers": results.get("skipped", []),
        }
    else:
        _import_status = {"done": False, "imported": [], "errors": [], "skipped": [], "total": len(tickers)}
        thread = threading.Thread(target=_bulk_import_worker, args=(tickers, _import_status, current_user.id), daemon=True)
        thread.start()
        return {
            "success": True,
            "message": f"Importing {len(tickers)} stocks in background. Check /api/stocks/import-status for progress.",
            "total": len(tickers),
        }


@app.get("/api/stocks/import-status", summary="Check bulk import progress")
def import_status(current_user: User = Depends(get_current_user)):
    """Check the status of a running bulk import."""
    return {
        "done": _import_status.get("done", True),
        "total": _import_status.get("total", 0),
        "imported": len(_import_status.get("imported", [])),
        "skipped": len(_import_status.get("skipped", [])),
        "errors_count": len(_import_status.get("errors", [])),
        "errors": _import_status.get("errors", [])[:20],  # First 20 errors
        "imported_tickers": _import_status.get("imported", [])[-20:],  # Last 20 imported
    }


@app.post("/api/stocks/import-index", summary="Import all stocks from a market index")
def import_from_index(
    index: str = Query("sp500", description="Index name: sp500, nasdaq100, dow30"),
    current_user: User = Depends(get_current_user)):
    """Fetch all tickers from a major index and import them."""
    global _import_status
    index = index.lower().strip()
    if index not in ("sp500", "nasdaq100", "dow30", "russell2000"):
        raise HTTPException(400, f"Unknown index: {index}. Supported: sp500, nasdaq100, dow30")

    tickers = _fetch_index_tickers(index)
    if not tickers:
        raise HTTPException(500, f"Could not fetch tickers for {index}")

    # Filter already imported
    session = get_session()
    existing = {c.ticker for c in session.query(Company.ticker).all()}
    session.close()
    new_tickers = [t for t in tickers if t not in existing]

    if not new_tickers:
        return {
            "success": True,
            "message": f"All {len(tickers)} tickers from {index.upper()} are already imported.",
            "total_in_index": len(tickers),
            "already_imported": len(tickers),
            "new_to_import": 0,
        }

    _import_status = {"done": False, "imported": [], "errors": [], "skipped": [], "total": len(new_tickers)}
    thread = threading.Thread(target=_bulk_import_worker, args=(new_tickers, _import_status, current_user.id), daemon=True)
    thread.start()

    return {
        "success": True,
        "message": f"Importing {len(new_tickers)} new stocks from {index.upper()} in background.",
        "total_in_index": len(tickers),
        "already_imported": len(tickers) - len(new_tickers),
        "new_to_import": len(new_tickers),
    }


@app.get("/api/stocks/available-indices", summary="List available market indices")
def available_indices(current_user: User = Depends(get_current_user)):
    return {
        "indices": [
            {"id": "sp500", "name": "S&P 500", "description": "500 largest US companies", "approx_count": 503},
            {"id": "nasdaq100", "name": "NASDAQ 100", "description": "100 largest NASDAQ companies", "approx_count": 101},
            {"id": "dow30", "name": "Dow Jones 30", "description": "30 blue-chip companies", "approx_count": 30},
        ]
    }


# ── Records (Full Data View) ──────────────────────────────────────────────────

@app.get("/api/records", summary="List all stock records with all fields")
def list_records(
    search: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    sort_by: str = Query("ticker"),
    descending: bool = Query(False),
    limit: int = Query(100),
    offset: int = Query(0),
    current_user: User = Depends(get_current_user)):
    """Return all tracked stocks with Company + latest Valuation + latest Financials data."""
    session = get_session()
    try:
        query = session.query(Company).filter(Company.is_active == True)

        if search:
            search_term = f"%{search.upper()}%"
            query = query.filter(
                (Company.ticker.like(search_term)) | (Company.name.like(f"%{search}%"))
            )
        if sector:
            query = query.filter(Company.sector == sector)

        # Sorting
        sort_col = getattr(Company, sort_by, Company.ticker)
        query = query.order_by(sort_col.desc() if descending else sort_col.asc())

        total = query.count()
        companies = query.offset(offset).limit(limit).all()

        # Load custom fields for Stock (object_id IS NULL)
        custom_fields = session.query(CustomField).filter(CustomField.user_id == current_user.id, CustomField.object_id == None, CustomField.is_active == True).order_by(CustomField.display_order).all()

        records = []
        for company in companies:
            # Get latest valuation
            val = get_latest_valuation(session, company.id)
            # Get latest financials
            fin = session.query(QuarterlyFinancials).filter(
                QuarterlyFinancials.company_id == company.id
            ).order_by(QuarterlyFinancials.period_end.desc()).first()

            record = build_record_data(company, val, fin)

            # Evaluate custom fields
            cf_values = {}
            for cf in custom_fields:
                if cf.field_type == "formula" and cf.formula:
                    cf_values[cf.name] = evaluate_formula(cf.formula, record)
                else:
                    # Non-formula stock fields could be loaded from somewhere else, but typically stocks only use formula fields
                    cf_values[cf.name] = None
            record["custom_fields"] = cf_values

            records.append(record)

        return {
            "records": records,
            "total": total,
            "limit": limit,
            "offset": offset,
            "custom_field_defs": [cf.to_dict() for cf in custom_fields],
        }
    finally:
        session.close()


@app.get("/api/records/{ticker}", summary="Get full record for a single stock")
def get_record(ticker: str, current_user: User = Depends(get_current_user)):
    """Return ALL available fields for a single stock."""
    session = get_session()
    try:
        company = session.query(Company).filter(Company.ticker == ticker.upper()).first()
        if not company:
            raise HTTPException(404, f"Stock {ticker} not found")

        val = get_latest_valuation(session, company.id)
        fin = session.query(QuarterlyFinancials).filter(
            QuarterlyFinancials.company_id == company.id
        ).order_by(QuarterlyFinancials.period_end.desc()).first()

        # Get ALL financials for history
        all_fins = session.query(QuarterlyFinancials).filter(
            QuarterlyFinancials.company_id == company.id
        ).order_by(QuarterlyFinancials.period_end.desc()).limit(20).all()

        # Get ALL valuations for history
        all_vals = session.query(Valuation).filter(
            Valuation.company_id == company.id
        ).order_by(Valuation.computed_at.desc()).limit(10).all()

        record = build_record_data(company, val, fin)

        # Custom fields for Stock
        custom_fields = session.query(CustomField).filter(CustomField.user_id == current_user.id, CustomField.object_id == None, CustomField.is_active == True).order_by(CustomField.display_order).all()
        cf_values = {}
        for cf in custom_fields:
            if cf.field_type == "formula" and cf.formula:
                cf_values[cf.name] = evaluate_formula(cf.formula, record)
            else:
                cf_values[cf.name] = None
        record["custom_fields"] = cf_values

        # Build field catalog
        all_field_names = sorted([
            k for k in record.keys()
            if k not in ("custom_fields",) and not k.startswith("val_") and not k.startswith("fin_")
        ])

        return {
            "record": record,
            "field_catalog": all_field_names,
            "financials_history": [f.to_dict() for f in all_fins],
            "valuation_history": [v.to_dict() for v in all_vals],
            "custom_field_defs": [cf.to_dict() for cf in custom_fields],
        }
    finally:
        session.close()


# ── Global Field Catalog (Standard Fields for Stocks) ─────────────────────────
COMPANY_FIELDS = [
    {"name": "ticker", "type": "text", "source": "Company"},
    {"name": "name", "type": "text", "source": "Company"},
    {"name": "sector", "type": "text", "source": "Company"},
    {"name": "industry", "type": "text", "source": "Company"},
    {"name": "country", "type": "text", "source": "Company"},
    {"name": "market_cap", "type": "currency", "source": "Company"},
    {"name": "beta", "type": "number", "source": "Company"},
    {"name": "shares_outstanding", "type": "number", "source": "Company"},
    {"name": "employees", "type": "number", "source": "Company"},
]

VALUATION_FIELDS = [
    {"name": "intrinsic_value_dcf", "type": "currency", "source": "Valuation"},
    {"name": "market_price", "type": "currency", "source": "Valuation"},
    {"name": "margin_of_safety_pct", "type": "percent", "source": "Valuation"},
    {"name": "fcf_growth_rate", "type": "percent", "source": "Valuation"},
    {"name": "terminal_growth_rate", "type": "percent", "source": "Valuation"},
    {"name": "wacc", "type": "percent", "source": "Valuation"},
    {"name": "capm_expected_return", "type": "percent", "source": "Valuation"},
    {"name": "altman_z_score", "type": "number", "source": "Valuation"},
    {"name": "z_score_zone", "type": "text", "source": "Valuation"},
    {"name": "signal", "type": "text", "source": "Valuation"},
    {"name": "enterprise_value", "type": "currency", "source": "Valuation"},
    {"name": "net_debt", "type": "currency", "source": "Valuation"},
    {"name": "equity_value", "type": "currency", "source": "Valuation"},
    {"name": "pv_of_fcfs", "type": "currency", "source": "Valuation"},
    {"name": "terminal_value", "type": "currency", "source": "Valuation"},
    {"name": "cost_of_equity", "type": "percent", "source": "Valuation"},
    {"name": "cost_of_debt", "type": "percent", "source": "Valuation"},
    {"name": "beta_used", "type": "number", "source": "Valuation"},
]

FINANCIALS_FIELDS = [
    {"name": "revenue", "type": "currency", "source": "Financials"},
    {"name": "net_income", "type": "currency", "source": "Financials"},
    {"name": "ebit", "type": "currency", "source": "Financials"},
    {"name": "ebitda", "type": "currency", "source": "Financials"},
    {"name": "free_cash_flow", "type": "currency", "source": "Financials"},
    {"name": "operating_cash_flow", "type": "currency", "source": "Financials"},
    {"name": "total_assets", "type": "currency", "source": "Financials"},
    {"name": "total_liabilities", "type": "currency", "source": "Financials"},
    {"name": "total_debt", "type": "currency", "source": "Financials"},
    {"name": "total_equity", "type": "currency", "source": "Financials"},
    {"name": "cash_and_equivalents", "type": "currency", "source": "Financials"},
    {"name": "retained_earnings", "type": "currency", "source": "Financials"},
    {"name": "current_assets", "type": "currency", "source": "Financials"},
    {"name": "current_liabilities", "type": "currency", "source": "Financials"},
    {"name": "working_capital", "type": "currency", "source": "Financials"},
    {"name": "capital_expenditure", "type": "currency", "source": "Financials"},
    {"name": "interest_expense", "type": "currency", "source": "Financials"},
    {"name": "gross_profit", "type": "currency", "source": "Financials"},
]

@app.get("/api/records/fields/catalog", summary="List all available fields for formula building")
def field_catalog(current_user: User = Depends(get_current_user)):
    """Return a catalog of all available fields across Company, Valuation, and Financials."""
    return {
        "company_fields": COMPANY_FIELDS,
        "valuation_fields": VALUATION_FIELDS,
        "financials_fields": FINANCIALS_FIELDS,
    }


# ── Admin: Dynamic Objects ────────────────────────────────────────────────────

from models import CustomObject, CustomRecord

@app.get("/api/admin/objects", summary="List all custom objects")
def list_custom_objects(current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        objects = session.query(CustomObject).filter(CustomObject.user_id == current_user.id).all()
        return {"objects": [obj.to_dict() for obj in objects]}
    finally:
        session.close()

@app.post("/api/admin/objects", summary="Create a custom object")
def create_custom_object(req: CustomObjectCreate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        existing = session.query(CustomObject).filter(CustomObject.name == req.name).first()
        if existing:
            raise HTTPException(400, f"Object '{req.name}' already exists.")
        
        obj = CustomObject(
            user_id=current_user.id,
            name=req.name, 
            label=req.label, 
            plural_label=req.plural_label, 
            description=req.description
        )
        session.add(obj)
        session.commit()
        return {"success": True, "object": obj.to_dict()}
    finally:
        session.close()

@app.put("/api/admin/objects/{object_id}", summary="Update a custom object")
def update_custom_object(object_id: int, req: CustomObjectUpdate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj = session.query(CustomObject).filter(CustomObject.id == object_id, CustomObject.user_id == current_user.id).first()
        if not obj:
            raise HTTPException(404, "Object not found")
        for k, v in req.dict(exclude_unset=True).items():
            setattr(obj, k, v)
        session.commit()
        return {"success": True, "object": obj.to_dict()}
    finally:
        session.close()

@app.delete("/api/admin/objects/{object_id}", summary="Delete a custom object")
def delete_custom_object(object_id: int, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj = session.query(CustomObject).filter(CustomObject.id == object_id, CustomObject.user_id == current_user.id).first()
        if not obj:
            raise HTTPException(404, "Object not found")
        session.delete(obj)
        session.commit()
        return {"success": True}
    finally:
        session.close()


# ── Admin: Custom Fields ──────────────────────────────────────────────────────

@app.get("/api/admin/fields", summary="List custom fields")
def list_custom_fields(object_id: Optional[int] = Query(None), current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        q = session.query(CustomField)
        if object_id is not None:
            # -1 can represent the "Stock" object (None) from the frontend
            if object_id == -1:
                q = q.filter(CustomField.object_id == None)
            else:
                q = q.filter(CustomField.object_id == object_id)
        fields = q.order_by(CustomField.display_order).all()
        results = [f.to_dict() for f in fields]

        if object_id in (-1, None):
            standard_fields = []
            idx = 1
            for cat_fields in [COMPANY_FIELDS, VALUATION_FIELDS, FINANCIALS_FIELDS]:
                for f in cat_fields:
                    label_str = " ".join(word.capitalize() for word in f["name"].split("_"))
                    standard_fields.append({
                        "id": f"std_{idx}",
                        "name": f["name"],
                        "label": label_str,
                        "field_type": f["type"],
                        "formula": None,
                        "lookup_object": None,
                        "is_active": True,
                        "description": f"Standard {f['source']} field",
                        "is_standard": True,
                        "display_order": 0,
                    })
                    idx += 1
            results = standard_fields + results

        return {"fields": results}
    finally:
        session.close()


@app.post("/api/admin/fields", summary="Create a custom field")
def create_custom_field(req: CustomFieldCreate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj_id = req.object_id if req.object_id != -1 else None
        
        # Validate name is unique for this object
        existing = session.query(CustomField).filter(
            CustomField.name == req.name,
            CustomField.object_id == obj_id
        ).first()
        if existing:
            raise HTTPException(400, f"Field '{req.name}' already exists for this object.")

        if req.field_type == "formula" and req.formula:
            try:
                compile(req.formula, "<formula>", "eval")
            except SyntaxError as e:
                raise HTTPException(400, f"Invalid formula syntax: {e}")

        field = CustomField(
            user_id=current_user.id,
            object_id=obj_id,
            name=req.name, label=req.label, field_type=req.field_type,
            formula=req.formula, lookup_object=req.lookup_object,
            description=req.description, format_decimals=req.format_decimals, 
            display_order=req.display_order,
        )
        session.add(field)
        session.commit()
        return {"success": True, "field": field.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


@app.put("/api/admin/fields/{field_id}", summary="Update a custom field")
def update_custom_field(field_id: int, req: CustomFieldUpdate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        field = session.query(CustomField).filter(CustomField.id == field_id, CustomField.user_id == current_user.id).first()
        if not field:
            raise HTTPException(404, f"Field ID {field_id} not found.")

        if req.formula is not None and field.field_type == "formula":
            try:
                compile(req.formula, "<formula>", "eval")
            except SyntaxError as e:
                raise HTTPException(400, f"Invalid formula syntax: {e}")

        for k, v in req.dict(exclude_unset=True).items():
            setattr(field, k, v)

        session.commit()
        return {"success": True, "field": field.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


@app.delete("/api/admin/fields/{field_id}", summary="Delete a custom field")
def delete_custom_field(field_id: int, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        field = session.query(CustomField).filter(CustomField.id == field_id, CustomField.user_id == current_user.id).first()
        if not field:
            raise HTTPException(404, f"Field ID {field_id} not found.")
        session.delete(field)
        session.commit()
        return {"success": True, "deleted": field.name}
    finally:
        session.close()


# ── Dynamic Records (Custom Objects Data) ─────────────────────────────────────

@app.get("/api/objects/{object_name}/records", summary="List records for a custom object")
def list_custom_records(object_name: str, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj = session.query(CustomObject).filter(CustomObject.name == object_name, CustomObject.user_id == current_user.id).first()
        if not obj:
            raise HTTPException(404, "Object not found")
        
        records = session.query(CustomRecord).filter(CustomRecord.object_id == obj.id, CustomRecord.user_id == current_user.id).order_by(CustomRecord.created_at.desc()).all()
        return {"records": [r.to_dict() for r in records]}
    finally:
        session.close()

@app.post("/api/objects/{object_name}/records", summary="Create a record for a custom object")
def create_custom_record(object_name: str, req: CustomRecordCreate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj = session.query(CustomObject).filter(CustomObject.name == object_name, CustomObject.user_id == current_user.id).first()
        if not obj:
            raise HTTPException(404, "Object not found")
            
        record = CustomRecord(
            user_id=current_user.id,
            object_id=obj.id, 
            name=req.name, 
            data=req.data
        )
        session.add(record)
        session.commit()
        return {"success": True, "record": record.to_dict()}
    finally:
        session.close()

@app.put("/api/objects/records/{record_id}", summary="Update a custom record")
def update_custom_record(record_id: int, req: CustomRecordUpdate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        record = session.query(CustomRecord).filter(CustomRecord.id == record_id, CustomRecord.user_id == current_user.id).first()
        if not record:
            raise HTTPException(404, "Record not found")
        
        if req.name is not None:
            record.name = req.name
        if req.data is not None:
            # Merge JSON
            current_data = record.data or {}
            current_data.update(req.data)
            record.data = current_data
            
        session.commit()
        return {"success": True, "record": record.to_dict()}
    finally:
        session.close()

@app.delete("/api/objects/records/{record_id}", summary="Delete a custom record")
def delete_custom_record(record_id: int, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        record = session.query(CustomRecord).filter(CustomRecord.id == record_id, CustomRecord.user_id == current_user.id).first()
        if not record:
            raise HTTPException(404, "Record not found")
        session.delete(record)
        session.commit()
        return {"success": True}
    finally:
        session.close()


# ── Admin: Page Layouts ───────────────────────────────────────────────────────

from models import PageLayout

@app.get("/api/admin/layouts", summary="List page layouts for an object")
def list_page_layouts(object_id: Optional[int] = Query(None), current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        q = session.query(PageLayout).filter(PageLayout.user_id == current_user.id)
        if object_id is not None:
            if object_id == -1:
                q = q.filter(PageLayout.object_id == None)
            else:
                q = q.filter(PageLayout.object_id == object_id)
        layouts = q.all()
        return {"layouts": [l.to_dict() for l in layouts]}
    finally:
        session.close()

@app.post("/api/admin/layouts", summary="Create a new page layout")
def create_page_layout(req: PageLayoutCreate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        obj_id = req.object_id if req.object_id != -1 else None
        layout = PageLayout(
            user_id=current_user.id,
            object_id=obj_id,
            name=req.name,
            layout_data=req.layout_data
        )
        session.add(layout)
        session.commit()
        return {"success": True, "layout": layout.to_dict()}
    finally:
        session.close()

@app.put("/api/admin/layouts/{layout_id}", summary="Update a page layout")
def update_page_layout(layout_id: int, req: PageLayoutUpdate, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        layout = session.query(PageLayout).filter(PageLayout.id == layout_id, PageLayout.user_id == current_user.id).first()
        if not layout:
            raise HTTPException(404, "Layout not found")
        if req.name is not None:
            layout.name = req.name
        if req.is_active is not None:
            layout.is_active = req.is_active
        if req.layout_data is not None:
            layout.layout_data = req.layout_data
        session.commit()
        return {"success": True, "layout": layout.to_dict()}
    finally:
        session.close()

@app.delete("/api/admin/layouts/{layout_id}", summary="Delete a page layout")
def delete_page_layout(layout_id: int, current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        layout = session.query(PageLayout).filter(PageLayout.id == layout_id, PageLayout.user_id == current_user.id).first()
        if not layout:
            raise HTTPException(404, "Layout not found")
        session.delete(layout)
        session.commit()
        return {"success": True}
    finally:
        session.close()


@app.post("/api/admin/fields/test", summary="Test a formula against a sample stock")
def test_formula(
    formula: str = Query(...),
    ticker: str = Query("AAPL"),
    current_user: User = Depends(get_current_user)):
    """Test a formula expression against real data from a stock."""
    try:
        compile(formula, "<formula>", "eval")
    except SyntaxError as e:
        raise HTTPException(400, f"Syntax error: {e}")

    session = get_session()
    try:
        company = session.query(Company).filter(Company.ticker == ticker.upper()).first()
        if not company:
            return {"result": None, "error": f"Stock {ticker} not found. Import it first."}

        val = get_latest_valuation(session, company.id)
        fin = session.query(QuarterlyFinancials).filter(
            QuarterlyFinancials.company_id == company.id
        ).order_by(QuarterlyFinancials.period_end.desc()).first()

        data = build_record_data(company, val, fin)
        result = evaluate_formula(formula, data)

        return {
            "result": result,
            "formula": formula,
            "ticker": ticker.upper(),
            "available_fields": sorted([k for k, v in data.items() if isinstance(v, (int, float)) and v is not None]),
        }
    finally:
        session.close()


@app.get("/api/admin/sectors", summary="List all unique sectors in the database")
def list_sectors(current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        sectors = session.query(Company.sector).filter(
            Company.sector.isnot(None), Company.is_active == True
        ).distinct().all()
        return {"sectors": sorted([s[0] for s in sectors if s[0]])}
    finally:
        session.close()


@app.get("/api/admin/stats", summary="Database statistics")
def admin_stats(current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        return {
            "companies": session.query(Company).filter(Company.is_active == True).count(),
            "total_companies": session.query(Company).count(),
            "valuations": session.query(Valuation).count(),
            "financials": session.query(QuarterlyFinancials).count(),
            "daily_prices": session.query(DailyPrice).count(),
            "custom_fields": session.query(CustomField).count(),
            "macro_rates": session.query(MacroRate).count(),
        }
    finally:
        session.close()


# ── Run entry point ───────────────────────────────────────────────────────────

# ── Data Providers ──────────────────────────────────────────────────────────────

from pydantic import BaseModel
from models import DataProvider

class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str = ""
    api_key: str = ""
    is_custom: bool = True

class ProviderUpdate(BaseModel):
    api_key: str = ""
    base_url: str = ""

@app.get("/api/admin/providers", summary="List all data providers")
def list_providers(current_user: User = Depends(get_current_user)):
    with get_session() as session:
        providers = session.query(DataProvider).all()
        return [p.to_dict() for p in providers]

@app.post("/api/admin/providers", summary="Add custom data provider")
def add_provider(req: ProviderCreate, current_user: User = Depends(get_current_user)):
    with get_session() as session:
        if session.query(DataProvider).filter_by(name=req.name).first():
            raise HTTPException(400, detail="Provider name already exists.")
        
        provider = DataProvider(
            name=req.name,
            provider_type=req.provider_type,
            base_url=req.base_url,
            api_key=req.api_key,
            is_custom=req.is_custom
        )
        session.add(provider)
        session.commit()
        return {"success": True, "provider": provider.to_dict()}

@app.put("/api/admin/providers/{provider_id}", summary="Update API Key")
def update_provider(provider_id: int, req: ProviderUpdate, current_user: User = Depends(get_current_user)):
    with get_session() as session:
        provider = session.query(DataProvider).filter_by(id=provider_id).first()
        if not provider:
            raise HTTPException(404, detail="Provider not found")
        
        provider.api_key = req.api_key
        provider.base_url = req.base_url
        session.commit()
        return {"success": True}

@app.put("/api/admin/providers/{provider_id}/activate", summary="Set active provider")
def activate_provider(provider_id: int, current_user: User = Depends(get_current_user)):
    with get_session() as session:
        provider = session.query(DataProvider).filter_by(id=provider_id).first()
        if not provider:
            raise HTTPException(404, detail="Provider not found")
            
        session.query(DataProvider).update({"is_active": False})
        provider.is_active = True
        session.commit()
        return {"success": True, "active_provider": provider.name}

class FormulaUpdateModel(BaseModel):
    formula_string: str

def validate_formula_ast(formula_str: str, available_vars: list[str]) -> tuple[bool, str]:
    """
    Validates a formula string using AST to prevent execution of malicious code or typos.
    Only allows basic math operators, specific functions, and predefined variables.
    """
    try:
        tree = ast.parse(formula_str, mode='eval')
    except SyntaxError as e:
        return False, f"Syntax Error: {e}"
    
    allowed_funcs = {"max", "min", "abs", "sum"}
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            # If the Name is a function call, we check it separately
            if node.id not in available_vars and node.id not in allowed_funcs:
                return False, f"Unknown or disallowed variable used: '{node.id}'"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in allowed_funcs:
                    return False, f"Function call not allowed: '{node.func.id}'"
    return True, "Valid"

@app.get("/api/admin/formulas", summary="List all math formulas")
def api_get_formulas(current_user: User = Depends(get_current_user)):
    with get_session() as session:
        formulas = session.query(MathFormula).all()
        return [f.to_dict() for f in formulas]

@app.post("/api/admin/formulas/{key}", summary="Update a math formula")
def api_update_formula(key: str, data: FormulaUpdateModel, current_user: User = Depends(get_current_user)):
    with get_session() as session:
        formula = session.query(MathFormula).filter_by(key=key).first()
        if not formula:
            raise HTTPException(status_code=404, detail="Formula not found")
            
        available = formula.available_variables.split(",") if formula.available_variables else []
        
        # Validate the new formula
        is_valid, error_msg = validate_formula_ast(data.formula_string, available)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
            
        formula.formula_string = data.formula_string
        session.commit()
        
        return {"success": True, "message": "Formula updated successfully", "formula": formula.to_dict()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
