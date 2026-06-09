"""
yfinance_client.py — Yahoo Finance Data Layer for Tőzsde Figyelő
Wraps the yfinance library to fetch stock data and compute technical indicators.
"""

import yfinance as yf
import numpy as np
import pandas as pd
import requests
from typing import Optional

# Setup custom session to avoid Yahoo Finance IP blocking on Datacenters
custom_session = requests.Session()
custom_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
})


# ── Ticker Metadata & Live Price ─────────────────────────────────────────────

def fetch_ticker_info(ticker: str) -> dict:
    """
    Fetch full company metadata and current price for a ticker.
    Returns a dict compatible with database.add_tracked_stock() and upsert_stock_cache().
    Raises ValueError if the ticker is invalid.
    """
    t = yf.Ticker(ticker.upper(), session=custom_session)
    info = t.info

    # Validate — yfinance returns minimal/empty info for unknown tickers
    if not info or info.get("trailingPegRatio") is None and not info.get("shortName") and not info.get("longName"):
        # Try a secondary check via fast_info
        try:
            price = t.fast_info.last_price
            if price is None:
                raise ValueError(f"Ticker '{ticker}' not found on Yahoo Finance.")
        except Exception:
            raise ValueError(f"Ticker '{ticker}' not found on Yahoo Finance.")

    def safe(key, default=None):
        val = info.get(key, default)
        return val if val is not None else default

    name = safe("longName") or safe("shortName") or ticker.upper()

    metadata = {
        "ticker":      ticker.upper(),
        "name":        name,
        "currency":    safe("currency", "USD"),
        "sector":      safe("sector", "Unknown"),
        "industry":    safe("industry", "Unknown"),
        "exchange":    safe("exchange", "Unknown"),
        "country":     safe("country", "Unknown"),
        "website":     safe("website", ""),
        "description": safe("longBusinessSummary", ""),
        "market_cap":  safe("marketCap"),
        "employees":   safe("fullTimeEmployees"),
    }

    cache_data = {
        "current_price":        safe("currentPrice") or safe("regularMarketPrice"),
        "previous_close":       safe("previousClose") or safe("regularMarketPreviousClose"),
        "day_open":             safe("open") or safe("regularMarketOpen"),
        "day_high":             safe("dayHigh") or safe("regularMarketDayHigh"),
        "day_low":              safe("dayLow") or safe("regularMarketDayLow"),
        "volume":               safe("volume") or safe("regularMarketVolume"),
        "market_cap":           safe("marketCap"),
        "pe_ratio":             safe("trailingPE"),
        "eps":                  safe("trailingEps"),
        "dividend_yield":       safe("dividendYield"),
        "fifty_two_week_high":  safe("fiftyTwoWeekHigh"),
        "fifty_two_week_low":   safe("fiftyTwoWeekLow"),
        "beta":                 safe("beta"),
    }

    return {"metadata": metadata, "cache": cache_data}


def fetch_live_price(ticker: str) -> dict:
    """Lightweight fetch of only the current price data (for refresh)."""
    t = yf.Ticker(ticker.upper(), session=custom_session)
    info = t.info

    def safe(key, default=None):
        val = info.get(key, default)
        return val if val is not None else default

    return {
        "current_price":        safe("currentPrice") or safe("regularMarketPrice"),
        "previous_close":       safe("previousClose") or safe("regularMarketPreviousClose"),
        "day_open":             safe("open") or safe("regularMarketOpen"),
        "day_high":             safe("dayHigh") or safe("regularMarketDayHigh"),
        "day_low":              safe("dayLow") or safe("regularMarketDayLow"),
        "volume":               safe("volume") or safe("regularMarketVolume"),
        "market_cap":           safe("marketCap"),
        "pe_ratio":             safe("trailingPE"),
        "eps":                  safe("trailingEps"),
        "dividend_yield":       safe("dividendYield"),
        "fifty_two_week_high":  safe("fiftyTwoWeekHigh"),
        "fifty_two_week_low":   safe("fiftyTwoWeekLow"),
        "beta":                 safe("beta"),
    }


# ── Historical OHLCV Data ─────────────────────────────────────────────────────

PERIOD_MAP = {
    "1mo":  "1mo",
    "3mo":  "3mo",
    "6mo":  "6mo",
    "1yr":  "1y",
    "2yr":  "2y",
    "5yr":  "5y",
    "max":  "max",
}

def fetch_history(ticker: str, period: str = "1yr") -> list[dict]:
    """
    Download daily OHLCV data for a ticker over the given period.
    Period options: '1mo', '3mo', '6mo', '1yr', '2yr', '5yr', 'max'
    Returns a list of dicts suitable for database.upsert_stock_history().
    """
    yf_period = PERIOD_MAP.get(period, "1y")
    t = yf.Ticker(ticker.upper(), session=custom_session)
    df = t.history(period=yf_period, auto_adjust=True)

    if df.empty:
        return []

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    records = []
    for _, row in df.iterrows():
        date_val = row.get("date") or row.get("datetime")
        if hasattr(date_val, "date"):
            date_str = date_val.date().isoformat()
        else:
            date_str = str(date_val)[:10]

        records.append({
            "ticker": ticker.upper(),
            "date":   date_str,
            "open":   float(row["open"]) if pd.notna(row.get("open")) else None,
            "high":   float(row["high"]) if pd.notna(row.get("high")) else None,
            "low":    float(row["low"])  if pd.notna(row.get("low"))  else None,
            "close":  float(row["close"]) if pd.notna(row.get("close")) else None,
            "volume": int(row["volume"]) if pd.notna(row.get("volume")) else None,
        })
    return records


# ── Technical Indicators ─────────────────────────────────────────────────────

def compute_indicators(history: list[dict]) -> dict:
    """
    Compute technical indicators from a list of OHLCV records.
    Returns arrays aligned with the input dates.
    Indicators: SMA20, SMA50, EMA20, RSI14, Bollinger Bands, MACD.
    """
    if len(history) < 20:
        return {}

    df = pd.DataFrame(history)
    close = df["close"].astype(float)
    dates = df["date"].tolist()

    # Simple Moving Averages
    sma20 = close.rolling(20).mean().round(4).tolist()
    sma50 = close.rolling(50).mean().round(4).tolist()

    # Exponential Moving Average
    ema20 = close.ewm(span=20, adjust=False).mean().round(4).tolist()

    # RSI (14-period)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).round(2).tolist()

    # Bollinger Bands (20-period, 2 std)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = (bb_mid + 2 * bb_std).round(4).tolist()
    bb_lower = (bb_mid - 2 * bb_std).round(4).tolist()
    bb_mid = bb_mid.round(4).tolist()

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = (ema12 - ema26).round(4).tolist()
    macd_signal = pd.Series(macd_line).ewm(span=9, adjust=False).mean().round(4).tolist()
    macd_hist = [round(m - s, 4) if m is not None and s is not None else None
                 for m, s in zip(macd_line, macd_signal)]

    def clean(lst):
        return [None if (v is not None and np.isnan(v)) else v for v in lst]

    return {
        "dates":        dates,
        "sma20":        clean(sma20),
        "sma50":        clean(sma50),
        "ema20":        clean(ema20),
        "rsi":          clean(rsi),
        "bb_upper":     clean(bb_upper),
        "bb_mid":       clean(bb_mid),
        "bb_lower":     clean(bb_lower),
        "macd_line":    clean(macd_line),
        "macd_signal":  clean(macd_signal),
        "macd_hist":    clean(macd_hist),
    }


# ── Portfolio Optimization ───────────────────────────────────────────────────

def compute_portfolio_optimization(histories: dict[str, list[dict]], num_portfolios: int = 3000) -> dict:
    """
    Simple Markowitz mean-variance portfolio optimization using Monte Carlo simulation.
    histories: {ticker: [OHLCV dicts]}
    Returns: efficient frontier points, optimal Sharpe portfolio, and min-variance portfolio.
    """
    import random

    tickers = list(histories.keys())
    n = len(tickers)
    if n < 2:
        return {"error": "Need at least 2 tickers for portfolio optimization."}

    # Build aligned returns DataFrame
    dfs = {}
    for tk, hist in histories.items():
        df = pd.DataFrame(hist)[["date", "close"]].set_index("date")["close"].astype(float)
        dfs[tk] = df

    prices = pd.DataFrame(dfs).dropna()
    if len(prices) < 30:
        return {"error": "Insufficient overlapping history for optimization (need 30+ days)."}

    returns = prices.pct_change().dropna()
    mean_returns = returns.mean() * 252          # annualized
    cov_matrix = returns.cov() * 252             # annualized

    results = {"returns": [], "volatility": [], "sharpe": [], "weights": []}
    risk_free = 0.04  # 4% risk-free rate assumption

    for _ in range(num_portfolios):
        w = np.random.dirichlet(np.ones(n))
        port_return = float(np.dot(w, mean_returns))
        port_vol    = float(np.sqrt(w @ cov_matrix.values @ w))
        sharpe      = (port_return - risk_free) / port_vol if port_vol > 0 else 0.0

        results["returns"].append(round(port_return, 4))
        results["volatility"].append(round(port_vol, 4))
        results["sharpe"].append(round(sharpe, 4))
        results["weights"].append({tk: round(float(wi), 4) for tk, wi in zip(tickers, w)})

    # Best Sharpe portfolio
    best_idx = int(np.argmax(results["sharpe"]))
    # Min volatility portfolio
    minvol_idx = int(np.argmin(results["volatility"]))

    return {
        "tickers":           tickers,
        "frontier":          {
            "returns":    results["returns"],
            "volatility": results["volatility"],
            "sharpe":     results["sharpe"],
        },
        "optimal_sharpe": {
            "weights":    results["weights"][best_idx],
            "return":     results["returns"][best_idx],
            "volatility": results["volatility"][best_idx],
            "sharpe":     results["sharpe"][best_idx],
        },
        "min_volatility": {
            "weights":    results["weights"][minvol_idx],
            "return":     results["returns"][minvol_idx],
            "volatility": results["volatility"][minvol_idx],
            "sharpe":     results["sharpe"][minvol_idx],
        },
    }


# ── Investment Strategy Signals ───────────────────────────────────────────────

def compute_signals(history: list[dict]) -> dict:
    """
    Generate buy/sell/hold signals based on multiple strategy models.
    """
    if len(history) < 50:
        return {"error": "Need at least 50 data points for signal generation."}

    df = pd.DataFrame(history)
    close = df["close"].astype(float)

    # --- Strategy 1: Golden/Death Cross (SMA 20/50) ---
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    current_sma20 = sma20.iloc[-1]
    prev_sma20    = sma20.iloc[-2]
    current_sma50 = sma50.iloc[-1]
    prev_sma50    = sma50.iloc[-2]

    if prev_sma20 < prev_sma50 and current_sma20 > current_sma50:
        cross_signal = "BUY"
        cross_reason = "Golden Cross: SMA20 crossed above SMA50"
    elif prev_sma20 > prev_sma50 and current_sma20 < current_sma50:
        cross_signal = "SELL"
        cross_reason = "Death Cross: SMA20 crossed below SMA50"
    elif current_sma20 > current_sma50:
        cross_signal = "HOLD_BULLISH"
        cross_reason = "SMA20 above SMA50 — uptrend"
    else:
        cross_signal = "HOLD_BEARISH"
        cross_reason = "SMA20 below SMA50 — downtrend"

    # --- Strategy 2: RSI Overbought/Oversold ---
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = (100 - (100 / (1 + rs)))
    current_rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    if current_rsi < 30:
        rsi_signal = "BUY"
        rsi_reason = f"RSI={current_rsi:.1f} — Oversold, potential reversal up"
    elif current_rsi > 70:
        rsi_signal = "SELL"
        rsi_reason = f"RSI={current_rsi:.1f} — Overbought, potential reversal down"
    else:
        rsi_signal = "NEUTRAL"
        rsi_reason = f"RSI={current_rsi:.1f} — Neutral zone"

    # --- Strategy 3: Bollinger Band squeeze ---
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    current_price = float(close.iloc[-1])

    if current_price < float(bb_lower.iloc[-1]):
        bb_signal = "BUY"
        bb_reason = "Price below lower Bollinger Band — oversold"
    elif current_price > float(bb_upper.iloc[-1]):
        bb_signal = "SELL"
        bb_reason = "Price above upper Bollinger Band — overbought"
    else:
        pct_b = (current_price - float(bb_lower.iloc[-1])) / (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1]) + 1e-9)
        bb_signal = "NEUTRAL"
        bb_reason = f"%B={pct_b:.2f} — Price within bands"

    # --- Strategy 4: MACD Signal ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal_line

    cur_hist  = float(macd_hist.iloc[-1])  if not pd.isna(macd_hist.iloc[-1])  else 0.0
    prev_hist = float(macd_hist.iloc[-2]) if not pd.isna(macd_hist.iloc[-2]) else 0.0

    if prev_hist < 0 and cur_hist > 0:
        macd_signal = "BUY"
        macd_reason = "MACD histogram crossed above zero — bullish momentum"
    elif prev_hist > 0 and cur_hist < 0:
        macd_signal = "SELL"
        macd_reason = "MACD histogram crossed below zero — bearish momentum"
    elif cur_hist > 0:
        macd_signal = "HOLD_BULLISH"
        macd_reason = f"MACD histogram positive ({cur_hist:+.4f}) — bullish"
    else:
        macd_signal = "HOLD_BEARISH"
        macd_reason = f"MACD histogram negative ({cur_hist:+.4f}) — bearish"

    # --- Overall composite signal ---
    buy_count  = sum(1 for s in [cross_signal, rsi_signal, bb_signal, macd_signal] if "BUY" in s)
    sell_count = sum(1 for s in [cross_signal, rsi_signal, bb_signal, macd_signal] if "SELL" in s)

    if buy_count >= 3:
        overall = "STRONG BUY"
    elif buy_count >= 2:
        overall = "BUY"
    elif sell_count >= 3:
        overall = "STRONG SELL"
    elif sell_count >= 2:
        overall = "SELL"
    else:
        overall = "NEUTRAL / HOLD"

    # 52-week stats
    week52_high = float(close.rolling(252).max().iloc[-1]) if len(close) >= 252 else float(close.max())
    week52_low  = float(close.rolling(252).min().iloc[-1]) if len(close) >= 252 else float(close.min())
    pct_from_52h = round((current_price - week52_high) / week52_high * 100, 2) if week52_high else None

    return {
        "current_price":   round(current_price, 4),
        "rsi":             round(current_rsi, 2),
        "overall_signal":  overall,
        "buy_signals":     buy_count,
        "sell_signals":    sell_count,
        "week52_high":     round(week52_high, 4),
        "week52_low":      round(week52_low, 4),
        "pct_from_52w_high": pct_from_52h,
        "strategies": {
            "moving_average_cross": {"signal": cross_signal, "reason": cross_reason},
            "rsi":                  {"signal": rsi_signal,   "reason": rsi_reason},
            "bollinger_bands":      {"signal": bb_signal,    "reason": bb_reason},
            "macd":                 {"signal": macd_signal,  "reason": macd_reason},
        },
    }
