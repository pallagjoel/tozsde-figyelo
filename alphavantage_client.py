"""
alphavantage_client.py — Alpha Vantage Data Layer
Integrates with the Alpha Vantage REST API for fetching stock data.
"""

import requests
from typing import Optional
import datetime

DEFAULT_BASE_URL = "https://www.alphavantage.co/query"

def fetch_ticker_info(ticker: str, api_key: str, base_url: str = "") -> dict:
    url = base_url or DEFAULT_BASE_URL
    res = requests.get(url, params={
        "function": "OVERVIEW",
        "symbol": ticker.upper(),
        "apikey": api_key
    })
    res.raise_for_status()
    data = res.json()
    
    if "Information" in data and "limit" in data["Information"].lower():
        raise ValueError("Alpha Vantage API rate limit exceeded.")
    if not data or "Symbol" not in data:
        raise ValueError(f"Ticker '{ticker}' not found on Alpha Vantage.")

    metadata = {
        "ticker": data.get("Symbol", ticker.upper()),
        "name": data.get("Name", ticker.upper()),
        "currency": data.get("Currency", "USD"),
        "sector": data.get("Sector", "Unknown"),
        "industry": data.get("Industry", "Unknown"),
        "exchange": data.get("Exchange", "Unknown"),
        "country": data.get("Country", "Unknown"),
        "website": "",
        "description": data.get("Description", ""),
        "market_cap": int(data.get("MarketCapitalization", 0)) if data.get("MarketCapitalization") and data.get("MarketCapitalization").isdigit() else None,
        "employees": int(data.get("FullTimeEmployees", 0)) if data.get("FullTimeEmployees") and data.get("FullTimeEmployees").isdigit() else None,
    }

    # Use GLOBAL_QUOTE for live cache data
    live_res = requests.get(url, params={
        "function": "GLOBAL_QUOTE",
        "symbol": ticker.upper(),
        "apikey": api_key
    })
    live_res.raise_for_status()
    live_data = live_res.json().get("Global Quote", {})

    def safe_float(val):
        try:
            return float(val) if val else None
        except:
            return None

    cache_data = {
        "current_price": safe_float(live_data.get("05. price")),
        "previous_close": safe_float(live_data.get("08. previous close")),
        "day_open": safe_float(live_data.get("02. open")),
        "day_high": safe_float(live_data.get("03. high")),
        "day_low": safe_float(live_data.get("04. low")),
        "volume": int(live_data.get("06. volume", 0)) if live_data.get("06. volume", "").isdigit() else None,
        "market_cap": metadata["market_cap"],
        "pe_ratio": safe_float(data.get("PERatio")),
        "eps": safe_float(data.get("EPS")),
        "dividend_yield": safe_float(data.get("DividendYield")),
        "fifty_two_week_high": safe_float(data.get("52WeekHigh")),
        "fifty_two_week_low": safe_float(data.get("52WeekLow")),
        "beta": safe_float(data.get("Beta")),
    }

    return {"metadata": metadata, "cache": cache_data}


def fetch_live_price(ticker: str, api_key: str, base_url: str = "") -> dict:
    url = base_url or DEFAULT_BASE_URL
    res = requests.get(url, params={
        "function": "GLOBAL_QUOTE",
        "symbol": ticker.upper(),
        "apikey": api_key
    })
    res.raise_for_status()
    data = res.json()
    
    if "Information" in data and "limit" in data["Information"].lower():
        raise ValueError("Alpha Vantage API rate limit exceeded.")
        
    live_data = data.get("Global Quote", {})
    if not live_data:
        raise ValueError(f"No live price data returned for {ticker}.")

    def safe_float(val):
        try:
            return float(val) if val else None
        except:
            return None

    return {
        "current_price": safe_float(live_data.get("05. price")),
        "previous_close": safe_float(live_data.get("08. previous close")),
        "day_open": safe_float(live_data.get("02. open")),
        "day_high": safe_float(live_data.get("03. high")),
        "day_low": safe_float(live_data.get("04. low")),
        "volume": int(live_data.get("06. volume", 0)) if live_data.get("06. volume", "").isdigit() else None,
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
    # Alpha vantage doesn't support 'max', '5yr' directly in the query period, 
    # outputsize=full returns up to 20 years. outputsize=compact returns 100 days.
    outputsize = "full" if period in ["1yr", "2yr", "5yr", "max", "6mo"] else "compact"
    
    res = requests.get(url, params={
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker.upper(),
        "outputsize": outputsize,
        "apikey": api_key
    })
    res.raise_for_status()
    data = res.json()
    
    if "Information" in data and "limit" in data["Information"].lower():
        raise ValueError("Alpha Vantage API rate limit exceeded.")

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        return []

    records = []
    for date_str, row in ts.items():
        records.append({
            "ticker": ticker.upper(),
            "date": date_str,
            "open": float(row.get("1. open")),
            "high": float(row.get("2. high")),
            "low": float(row.get("3. low")),
            "close": float(row.get("4. close")),
            "volume": int(row.get("5. volume", 0)),
        })
        
    # Sort chronologically
    records.sort(key=lambda x: x["date"])
    
    # Filter by period if necessary
    if period != "max":
        limit_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1yr": 365, "2yr": 730, "5yr": 1825}.get(period, 365)
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=limit_days)).strftime("%Y-%m-%d")
        records = [r for r in records if r["date"] >= cutoff]

    return records
