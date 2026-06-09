"""
data_factory.py — Multi-Database Routing Layer
Routes data requests to the active provider selected by the user.
"""

from typing import Optional
from models import get_session, DataProvider
import yfinance_client as yfc
import alphavantage_client as av_client
import fmp_client as fmp_client

def get_active_provider() -> dict:
    """Retrieve the currently active data provider configuration as a dictionary to avoid detached instances."""
    with get_session() as session:
        active = session.query(DataProvider).filter(DataProvider.is_active == True).first()
        if not active:
            return {"name": "Fallback", "provider_type": "yahoo", "api_key": "", "base_url": ""}
        return {"name": active.name, "provider_type": active.provider_type, "api_key": active.api_key, "base_url": active.base_url}

# ── Routing Functions ────────────────────────────────────────────────────────

def fetch_ticker_info(ticker: str) -> dict:
    provider = get_active_provider()
    
    if provider["provider_type"] == "alphavantage" and provider["api_key"]:
        try:
            print(f"[data_factory] Routing to Alpha Vantage for {ticker}")
            return av_client.fetch_ticker_info(ticker, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] Alpha Vantage failed ({e}), falling back to Yahoo Finance")
            
    elif provider["provider_type"] == "fmp" and provider["api_key"]:
        try:
            print(f"[data_factory] Routing to FMP for {ticker}")
            return fmp_client.fetch_ticker_info(ticker, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] FMP failed ({e}), falling back to Yahoo Finance")
            
    elif provider["provider_type"] == "custom":
        print(f"[data_factory] Routing to Custom Provider '{provider['name']}' for {ticker}")
        # Custom provider fallback is currently Yahoo Finance until webhooks are supported
        pass
        
    # Default: Yahoo Finance
    return yfc.fetch_ticker_info(ticker)


def fetch_live_price(ticker: str) -> dict:
    provider = get_active_provider()
    
    if provider["provider_type"] == "alphavantage" and provider["api_key"]:
        try:
            return av_client.fetch_live_price(ticker, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] Alpha Vantage failed ({e}), falling back to Yahoo Finance")
            
    elif provider["provider_type"] == "fmp" and provider["api_key"]:
        try:
            return fmp_client.fetch_live_price(ticker, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] FMP failed ({e}), falling back to Yahoo Finance")
            
    # Fallback to yahoo
    return yfc.fetch_live_price(ticker)


def fetch_history(ticker: str, period: str = "1yr") -> list[dict]:
    provider = get_active_provider()
    
    if provider["provider_type"] == "alphavantage" and provider["api_key"]:
        try:
            return av_client.fetch_history(ticker, period, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] Alpha Vantage failed ({e}), falling back to Yahoo Finance")
            
    elif provider["provider_type"] == "fmp" and provider["api_key"]:
        try:
            return fmp_client.fetch_history(ticker, period, provider["api_key"], provider["base_url"])
        except Exception as e:
            print(f"[data_factory] FMP failed ({e}), falling back to Yahoo Finance")
            
    # Fallback to yahoo
    return yfc.fetch_history(ticker, period)


def compute_technical_indicators(df) -> dict:
    # Technical indicators are mathematical, not tied to a specific provider
    return yfc.compute_technical_indicators(df)

def compute_portfolio_optimization(histories: dict) -> dict:
    return yfc.compute_portfolio_optimization(histories)

