"""
fmp_client.py — Financial Modeling Prep Data Layer
Integrates with the FMP REST API for fetching stock data.
"""

import requests
import datetime

DEFAULT_BASE_URL = "https://financialmodelingprep.com/api/v3"

def fetch_ticker_info(ticker: str, api_key: str, base_url: str = "") -> dict:
    url = base_url or DEFAULT_BASE_URL
    
    # 1. Profile for metadata
    prof_res = requests.get(f"{url}/profile/{ticker.upper()}", params={"apikey": api_key})
    prof_res.raise_for_status()
    prof_data = prof_res.json()
    
    if not prof_data:
        raise ValueError(f"Ticker '{ticker}' not found on FMP.")
    if isinstance(prof_data, dict) and "Error Message" in prof_data:
        raise ValueError(f"FMP API Error: {prof_data['Error Message']}")

    profile = prof_data[0]

    metadata = {
        "ticker": profile.get("symbol", ticker.upper()),
        "name": profile.get("companyName", ticker.upper()),
        "currency": profile.get("currency", "USD"),
        "sector": profile.get("sector", "Unknown"),
        "industry": profile.get("industry", "Unknown"),
        "exchange": profile.get("exchangeShortName", "Unknown"),
        "country": profile.get("country", "Unknown"),
        "website": profile.get("website", ""),
        "description": profile.get("description", ""),
        "market_cap": int(profile.get("mktCap", 0)) if profile.get("mktCap") else None,
        "employees": int(profile.get("fullTimeEmployees", 0)) if profile.get("fullTimeEmployees", "").isdigit() else None,
    }

    # 2. Quote for cache data
    quote_res = requests.get(f"{url}/quote/{ticker.upper()}", params={"apikey": api_key})
    quote_res.raise_for_status()
    quote_data = quote_res.json()

    quote = quote_data[0] if quote_data else {}

    def safe_float(val):
        try:
            return float(val) if val is not None else None
        except:
            return None

    cache_data = {
        "current_price": safe_float(quote.get("price")),
        "previous_close": safe_float(quote.get("previousClose")),
        "day_open": safe_float(quote.get("open")),
        "day_high": safe_float(quote.get("dayHigh")),
        "day_low": safe_float(quote.get("dayLow")),
        "volume": int(quote.get("volume", 0)) if quote.get("volume") else None,
        "market_cap": safe_float(quote.get("marketCap")),
        "pe_ratio": safe_float(quote.get("pe")),
        "eps": safe_float(quote.get("eps")),
        "dividend_yield": None, # Not always easily available in free quote
        "fifty_two_week_high": safe_float(quote.get("yearHigh")),
        "fifty_two_week_low": safe_float(quote.get("yearLow")),
        "beta": safe_float(profile.get("beta")),
    }

    return {"metadata": metadata, "cache": cache_data}


def fetch_live_price(ticker: str, api_key: str, base_url: str = "") -> dict:
    url = base_url or DEFAULT_BASE_URL
    res = requests.get(f"{url}/quote-short/{ticker.upper()}", params={"apikey": api_key})
    res.raise_for_status()
    data = res.json()
    
    if isinstance(data, dict) and "Error Message" in data:
        raise ValueError(f"FMP API Error: {data['Error Message']}")

    if not data:
        raise ValueError(f"No live price data returned for {ticker}.")

    quote = data[0]

    def safe_float(val):
        try:
            return float(val) if val is not None else None
        except:
            return None

    return {
        "current_price": safe_float(quote.get("price")),
        "volume": int(quote.get("volume", 0)) if quote.get("volume") else None,
        # Quote short doesn't have previous close and OHLC
        "previous_close": None,
        "day_open": None,
        "day_high": None,
        "day_low": None,
        "market_cap": None,
        "pe_ratio": None,
        "eps": None,
        "dividend_yield": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
        "beta": None,
    }


def fetch_history(ticker: str, period: str, api_key: str, base_url: str = "") -> list[dict]:
    url = base_url or DEFAULT_BASE_URL
    
    # FMP's historical-price-full returns up to 5 years by default.
    res = requests.get(f"{url}/historical-price-full/{ticker.upper()}", params={"apikey": api_key})
    res.raise_for_status()
    data = res.json()
    
    if isinstance(data, dict) and "Error Message" in data:
        raise ValueError(f"FMP API Error: {data['Error Message']}")

    ts = data.get("historical", [])
    if not ts:
        return []

    records = []
    for row in ts:
        records.append({
            "ticker": ticker.upper(),
            "date": row.get("date"),
            "open": float(row.get("open")),
            "high": float(row.get("high")),
            "low": float(row.get("low")),
            "close": float(row.get("close")),
            "volume": int(row.get("volume", 0)),
        })
        
    # Sort chronologically (FMP returns newest first)
    records.sort(key=lambda x: x["date"])
    
    # Filter by period if necessary
    if period != "max":
        limit_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1yr": 365, "2yr": 730, "5yr": 1825}.get(period, 365)
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=limit_days)).strftime("%Y-%m-%d")
        records = [r for r in records if r["date"] >= cutoff]

    return records
