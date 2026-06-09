"""
data_factory.py — Multi-Database Routing Layer
Routes data requests to the active provider selected by the user.
"""

from typing import Optional
from models import get_session, DataProvider
import yfinance_client as yfc

# Currently supported providers (in the future, these can be split into separate modules)
# We use Yahoo Finance as the robust fallback/default.

def get_active_provider() -> DataProvider:
    """Retrieve the currently active data provider configuration."""
    with get_session() as session:
        active = session.query(DataProvider).filter(DataProvider.is_active == True).first()
        if not active:
            # Fallback to Yahoo Finance if nothing is active
            return DataProvider(name="Fallback", provider_type="yahoo")
        return active

# ── Routing Functions ────────────────────────────────────────────────────────

def fetch_ticker_info(ticker: str) -> dict:
    provider = get_active_provider()
    
    # Example logic for routing (currently everything falls back to YFC 
    # until the specific provider plugins are fully coded out, but this is the architecture)
    
    if provider.provider_type == "alphavantage":
        # TODO: Implement Alpha Vantage specific fetching
        print(f"[data_factory] Routing to Alpha Vantage for {ticker}")
        return yfc.fetch_ticker_info(ticker) # Fallback for now
        
    elif provider.provider_type == "fmp":
        # TODO: Implement FMP specific fetching
        print(f"[data_factory] Routing to FMP for {ticker}")
        return yfc.fetch_ticker_info(ticker) # Fallback for now
        
    elif provider.provider_type == "custom":
        print(f"[data_factory] Routing to Custom Provider '{provider.name}' for {ticker}")
        return yfc.fetch_ticker_info(ticker)
        
    else:
        # Default: Yahoo Finance
        return yfc.fetch_ticker_info(ticker)


def fetch_live_price(ticker: str) -> dict:
    provider = get_active_provider()
    if provider.provider_type == "yahoo":
        return yfc.fetch_live_price(ticker)
    
    # Fallback to yahoo for missing implementations
    return yfc.fetch_live_price(ticker)


def fetch_history(ticker: str, period: str = "1yr") -> list[dict]:
    provider = get_active_provider()
    if provider.provider_type == "yahoo":
        return yfc.fetch_history(ticker, period)
    
    # Fallback to yahoo for missing implementations
    return yfc.fetch_history(ticker, period)


def compute_technical_indicators(df) -> dict:
    # Technical indicators are mathematical, not tied to a specific provider
    return yfc.compute_technical_indicators(df)

def compute_portfolio_optimization(histories: dict) -> dict:
    return yfc.compute_portfolio_optimization(histories)

