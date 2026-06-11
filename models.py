"""
models.py — SQLAlchemy 2.0 ORM Models for Tőzsde Figyelő Quant Engine
Parent-Child (Master-Detail) Relational Hierarchy

Schema:
    Company (Parent)
      ├── QuarterlyFinancials (Child) — SEC fundamental data
      ├── DailyPrice (Child) — OHLCV historical data
      └── Valuation (Child) — Computed DCF / Z-score / CAPM
    MacroRate (Standalone) — Risk-free rate, market return, inflation
"""

from __future__ import annotations

import os
from datetime import datetime, date, timezone
from typing import List, Optional

from sqlalchemy import (
    create_engine, String, Float, Integer, Text, Date, DateTime, Boolean, JSON,
    ForeignKey, UniqueConstraint, Index, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, Session, sessionmaker,
)

# ── Database Path ─────────────────────────────────────────────────────────────

DB_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "market_data.db")
DB_URL  = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

# Enable WAL mode and foreign keys for SQLite
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()


# ── Base ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ══════════════════════════════════════════════════════════════════════════════
# USER (Authentication & Multi-Tenant)
# ══════════════════════════════════════════════════════════════════════════════

class User(Base):
    """
    User account for authentication and data isolation.
    """
    __tablename__ = "users"

    id:             Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    email:          Mapped[str]           = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash:  Mapped[str]           = mapped_column(String(255), nullable=False)
    mfa_secret:     Mapped[Optional[str]] = mapped_column(String(32))
    mfa_enabled:    Mapped[bool]          = mapped_column(Boolean, default=False)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login:     Mapped[Optional[datetime]] = mapped_column(DateTime)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "email": self.email, "mfa_enabled": self.mfa_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ══════════════════════════════════════════════════════════════════════════════
# PARENT: Company (Master Record)
# ══════════════════════════════════════════════════════════════════════════════

class Company(Base):
    """
    Master record for a tracked equity.
    All child tables (financials, prices, valuations) reference this via FK.
    """
    __tablename__ = "companies"

    id:                  Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    ticker:              Mapped[str]           = mapped_column(String(20), unique=True, nullable=False, index=True)
    name:                Mapped[Optional[str]] = mapped_column(String(200))
    sector:              Mapped[Optional[str]] = mapped_column(String(100))
    industry:            Mapped[Optional[str]] = mapped_column(String(200))
    country:             Mapped[Optional[str]] = mapped_column(String(100))
    exchange:            Mapped[Optional[str]] = mapped_column(String(50))
    currency:            Mapped[Optional[str]] = mapped_column(String(10), default="USD")
    website:             Mapped[Optional[str]] = mapped_column(String(300))
    description:         Mapped[Optional[str]] = mapped_column(Text)
    shares_outstanding:  Mapped[Optional[float]] = mapped_column(Float)
    market_cap:          Mapped[Optional[float]] = mapped_column(Float)
    beta:                Mapped[Optional[float]] = mapped_column(Float)
    employees:           Mapped[Optional[int]]   = mapped_column(Integer)
    is_active:           Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:          Mapped[datetime]       = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at:          Mapped[datetime]       = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc),
                                                                onupdate=lambda: datetime.now(timezone.utc))

    # ── Relationships (One-to-Many) ──
    quarterly_financials: Mapped[List["QuarterlyFinancials"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="QuarterlyFinancials.period_end.desc()"
    )
    daily_prices: Mapped[List["DailyPrice"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="DailyPrice.date.desc()"
    )
    valuations: Mapped[List["Valuation"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="Valuation.computed_at.desc()"
    )

    def __repr__(self) -> str:
        return f"<Company(ticker='{self.ticker}', name='{self.name}')>"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "ticker": self.ticker, "name": self.name,
            "sector": self.sector, "industry": self.industry, "country": self.country,
            "exchange": self.exchange, "currency": self.currency,
            "shares_outstanding": self.shares_outstanding, "market_cap": self.market_cap,
            "beta": self.beta, "is_active": self.is_active,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CHILD 1: Quarterly Financials (SEC-Level Fundamental Data)
# ══════════════════════════════════════════════════════════════════════════════

class QuarterlyFinancials(Base):
    """
    Cleaned quarterly fundamental data from SEC filings (via FMP / yfinance fallback).
    These are the raw inputs to the DCF and Altman Z-score computations.
    """
    __tablename__ = "quarterly_financials"
    __table_args__ = (
        UniqueConstraint("company_id", "period_end", name="uq_company_period"),
        Index("idx_qf_company_period", "company_id", "period_end"),
    )

    id:                    Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    company_id:            Mapped[int]           = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    period_end:            Mapped[date]          = mapped_column(Date, nullable=False)   # End of fiscal quarter
    fiscal_year:           Mapped[Optional[int]] = mapped_column(Integer)
    fiscal_quarter:        Mapped[Optional[int]] = mapped_column(Integer)               # 1, 2, 3, or 4

    # ── Income Statement ──
    revenue:               Mapped[Optional[float]] = mapped_column(Float)
    cost_of_revenue:       Mapped[Optional[float]] = mapped_column(Float)
    gross_profit:          Mapped[Optional[float]] = mapped_column(Float)
    operating_income:      Mapped[Optional[float]] = mapped_column(Float)               # EBIT proxy
    ebit:                  Mapped[Optional[float]] = mapped_column(Float)
    ebitda:                Mapped[Optional[float]] = mapped_column(Float)
    net_income:            Mapped[Optional[float]] = mapped_column(Float)
    interest_expense:      Mapped[Optional[float]] = mapped_column(Float)
    income_tax_expense:    Mapped[Optional[float]] = mapped_column(Float)

    # ── Balance Sheet ──
    total_assets:          Mapped[Optional[float]] = mapped_column(Float)
    total_liabilities:     Mapped[Optional[float]] = mapped_column(Float)
    current_assets:        Mapped[Optional[float]] = mapped_column(Float)
    current_liabilities:   Mapped[Optional[float]] = mapped_column(Float)
    total_equity:          Mapped[Optional[float]] = mapped_column(Float)
    retained_earnings:     Mapped[Optional[float]] = mapped_column(Float)
    total_debt:            Mapped[Optional[float]] = mapped_column(Float)
    cash_and_equivalents:  Mapped[Optional[float]] = mapped_column(Float)

    # ── Cash Flow Statement ──
    operating_cash_flow:   Mapped[Optional[float]] = mapped_column(Float)
    capital_expenditure:   Mapped[Optional[float]] = mapped_column(Float)
    free_cash_flow:        Mapped[Optional[float]] = mapped_column(Float)
    depreciation:          Mapped[Optional[float]] = mapped_column(Float)

    # ── Meta ──
    data_source:           Mapped[Optional[str]]  = mapped_column(String(20), default="fmp")  # "fmp" or "yfinance"
    loaded_at:             Mapped[datetime]       = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationship ──
    company: Mapped["Company"] = relationship(back_populates="quarterly_financials")

    def __repr__(self) -> str:
        return f"<QuarterlyFinancials(company_id={self.company_id}, period={self.period_end})>"

    @property
    def working_capital(self) -> Optional[float]:
        """Current Assets - Current Liabilities (for Altman Z-score X1)."""
        if self.current_assets is not None and self.current_liabilities is not None:
            return self.current_assets - self.current_liabilities
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "company_id": self.company_id,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "fiscal_year": self.fiscal_year, "fiscal_quarter": self.fiscal_quarter,
            "revenue": self.revenue, "net_income": self.net_income,
            "ebit": self.ebit, "ebitda": self.ebitda,
            "total_assets": self.total_assets, "total_liabilities": self.total_liabilities,
            "current_assets": self.current_assets, "current_liabilities": self.current_liabilities,
            "retained_earnings": self.retained_earnings,
            "total_debt": self.total_debt, "cash_and_equivalents": self.cash_and_equivalents,
            "free_cash_flow": self.free_cash_flow, "operating_cash_flow": self.operating_cash_flow,
            "capital_expenditure": self.capital_expenditure, "working_capital": self.working_capital,
            "interest_expense": self.interest_expense, "data_source": self.data_source,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CHILD 2: Daily Prices (OHLCV Market Data)
# ══════════════════════════════════════════════════════════════════════════════

class DailyPrice(Base):
    """
    Historical daily OHLCV price data from Yahoo Finance.
    Used for beta regression, CAPM, and mean-reversion calculations.
    """
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("company_id", "date", name="uq_company_date"),
        Index("idx_dp_company_date", "company_id", "date"),
    )

    id:         Mapped[int]             = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int]             = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    date:       Mapped[date]            = mapped_column(Date, nullable=False)
    open:       Mapped[Optional[float]] = mapped_column(Float)
    high:       Mapped[Optional[float]] = mapped_column(Float)
    low:        Mapped[Optional[float]] = mapped_column(Float)
    close:      Mapped[Optional[float]] = mapped_column(Float)
    volume:     Mapped[Optional[int]]   = mapped_column(Integer)
    adj_close:  Mapped[Optional[float]] = mapped_column(Float)

    # ── Relationship ──
    company: Mapped["Company"] = relationship(back_populates="daily_prices")

    def __repr__(self) -> str:
        return f"<DailyPrice(company_id={self.company_id}, date={self.date}, close={self.close})>"


# ══════════════════════════════════════════════════════════════════════════════
# CHILD 3: Valuations (Nightly Computed Intrinsic Values)
# ══════════════════════════════════════════════════════════════════════════════

class Valuation(Base):
    """
    Nightly-computed valuation metrics.
    All math runs against the local DB — zero external API calls.
    """
    __tablename__ = "valuations"
    __table_args__ = (
        Index("idx_val_company", "company_id"),
        Index("idx_val_signal", "signal"),
    )

    id:                    Mapped[int]             = mapped_column(primary_key=True, autoincrement=True)
    company_id:            Mapped[int]             = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    # ── DCF Model Outputs ──
    intrinsic_value_dcf:   Mapped[Optional[float]] = mapped_column(Float)   # Per-share intrinsic value
    market_price:          Mapped[Optional[float]] = mapped_column(Float)   # Market price at computation time
    margin_of_safety_pct:  Mapped[Optional[float]] = mapped_column(Float)   # (Intrinsic - Market) / Intrinsic * 100

    # ── DCF Breakdown (for audit) ──
    fcf_growth_rate:       Mapped[Optional[float]] = mapped_column(Float)   # Historical CAGR used
    terminal_growth_rate:  Mapped[Optional[float]] = mapped_column(Float)   # Terminal growth used
    pv_of_fcfs:            Mapped[Optional[float]] = mapped_column(Float)   # Sum of discounted FCFs
    terminal_value:        Mapped[Optional[float]] = mapped_column(Float)   # TV before discounting
    pv_of_terminal:        Mapped[Optional[float]] = mapped_column(Float)   # Discounted terminal value
    enterprise_value:      Mapped[Optional[float]] = mapped_column(Float)   # PV_FCFs + PV_TV
    net_debt:              Mapped[Optional[float]] = mapped_column(Float)   # Total Debt - Cash
    equity_value:          Mapped[Optional[float]] = mapped_column(Float)   # EV - Net Debt

    # ── CAPM ──
    capm_expected_return:  Mapped[Optional[float]] = mapped_column(Float)   # E(Ri) annualized
    risk_free_rate:        Mapped[Optional[float]] = mapped_column(Float)
    equity_risk_premium:   Mapped[Optional[float]] = mapped_column(Float)
    beta_used:             Mapped[Optional[float]] = mapped_column(Float)

    # ── WACC ──
    wacc:                  Mapped[Optional[float]] = mapped_column(Float)
    cost_of_equity:        Mapped[Optional[float]] = mapped_column(Float)
    cost_of_debt:          Mapped[Optional[float]] = mapped_column(Float)

    # ── Altman Z-Score ──
    altman_z_score:        Mapped[Optional[float]] = mapped_column(Float)
    z_score_zone:          Mapped[Optional[str]]   = mapped_column(String(20))  # SAFE / GREY / DISTRESS
    z_x1:                  Mapped[Optional[float]] = mapped_column(Float)       # WC / TA
    z_x2:                  Mapped[Optional[float]] = mapped_column(Float)       # RE / TA
    z_x3:                  Mapped[Optional[float]] = mapped_column(Float)       # EBIT / TA
    z_x4:                  Mapped[Optional[float]] = mapped_column(Float)       # Mkt Cap / TL
    z_x5:                  Mapped[Optional[float]] = mapped_column(Float)       # Revenue / TA

    # ── Signal ──
    signal:                Mapped[Optional[str]]   = mapped_column(String(30))  # STRONG_BUY / BUY / HOLD / OVERVALUED / VALUE_TRAP / INSUFFICIENT_DATA
    signal_reason:         Mapped[Optional[str]]   = mapped_column(Text)

    # ── Structural Break Warnings ──
    revenue_decline_flag:  Mapped[bool]            = mapped_column(Boolean, default=False)
    negative_fcf_flag:     Mapped[bool]            = mapped_column(Boolean, default=False)
    data_quality:          Mapped[Optional[str]]   = mapped_column(String(30), default="FULL")  # FULL / ESTIMATED / PARTIAL

    # ── Meta ──
    computed_at:           Mapped[datetime]        = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ── Relationship ──
    company: Mapped["Company"] = relationship(back_populates="valuations")

    def __repr__(self) -> str:
        return f"<Valuation(company_id={self.company_id}, signal='{self.signal}', z={self.altman_z_score})>"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "company_id": self.company_id,
            "intrinsic_value_dcf": self.intrinsic_value_dcf,
            "market_price": self.market_price,
            "margin_of_safety_pct": self.margin_of_safety_pct,
            "fcf_growth_rate": self.fcf_growth_rate,
            "terminal_growth_rate": self.terminal_growth_rate,
            "pv_of_fcfs": self.pv_of_fcfs,
            "terminal_value": self.terminal_value,
            "pv_of_terminal": self.pv_of_terminal,
            "enterprise_value": self.enterprise_value,
            "net_debt": self.net_debt,
            "equity_value": self.equity_value,
            "capm_expected_return": self.capm_expected_return,
            "risk_free_rate": self.risk_free_rate,
            "equity_risk_premium": self.equity_risk_premium,
            "beta_used": self.beta_used,
            "wacc": self.wacc,
            "cost_of_equity": self.cost_of_equity,
            "cost_of_debt": self.cost_of_debt,
            "altman_z_score": self.altman_z_score,
            "z_score_zone": self.z_score_zone,
            "z_x1": self.z_x1, "z_x2": self.z_x2, "z_x3": self.z_x3,
            "z_x4": self.z_x4, "z_x5": self.z_x5,
            "signal": self.signal, "signal_reason": self.signal_reason,
            "revenue_decline_flag": self.revenue_decline_flag,
            "negative_fcf_flag": self.negative_fcf_flag,
            "data_quality": self.data_quality,
            "computed_at": self.computed_at.isoformat() if self.computed_at else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE: Macro Rates (FRED Data)
# ══════════════════════════════════════════════════════════════════════════════

class MacroRate(Base):
    """
    Macroeconomic rates pulled from FRED.
    Used as inputs for CAPM and WACC calculations.
    """
    __tablename__ = "macro_rates"

    id:                    Mapped[int]             = mapped_column(primary_key=True, autoincrement=True)
    date:                  Mapped[date]            = mapped_column(Date, unique=True, nullable=False, index=True)
    risk_free_rate:        Mapped[Optional[float]] = mapped_column(Float)   # 10Y Treasury yield (DGS10)
    market_return:         Mapped[Optional[float]] = mapped_column(Float)   # Annualized S&P 500 return
    inflation_rate:        Mapped[Optional[float]] = mapped_column(Float)   # CPI YoY
    equity_risk_premium:   Mapped[Optional[float]] = mapped_column(Float)   # Rm - Rf
    fed_funds_rate:        Mapped[Optional[float]] = mapped_column(Float)   # FEDFUNDS effective rate
    loaded_at:             Mapped[datetime]        = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<MacroRate(date={self.date}, rf={self.risk_free_rate})>"

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat() if self.date else None,
            "risk_free_rate": self.risk_free_rate,
            "market_return": self.market_return,
            "inflation_rate": self.inflation_rate,
            "equity_risk_premium": self.equity_risk_premium,
            "fed_funds_rate": self.fed_funds_rate,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DATA PROVIDERS (Multi-database routing)
# ══════════════════════════════════════════════════════════════════════════════

class DataProvider(Base):
    """
    Configuration for external data sources (Yahoo, Alpha Vantage, FMP, Custom).
    """
    __tablename__ = "data_providers"

    id:             Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    name:           Mapped[str]           = mapped_column(String(100), nullable=False, unique=True)
    provider_type:  Mapped[str]           = mapped_column(String(50), nullable=False) # e.g. "yahoo", "alphavantage", "fmp", "custom"
    base_url:       Mapped[Optional[str]] = mapped_column(String(255))
    api_key:        Mapped[Optional[str]] = mapped_column(String(255))
    is_active:      Mapped[bool]          = mapped_column(Boolean, default=False)
    is_custom:      Mapped[bool]          = mapped_column(Boolean, default=False)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "provider_type": self.provider_type,
            "base_url": self.base_url, "api_key": self.api_key,
            "is_active": self.is_active, "is_custom": self.is_custom
        }


# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC METADATA (Admin Platform / Salesforce-style architecture)
# ══════════════════════════════════════════════════════════════════════════════

class CustomObject(Base):
    """
    User-defined dynamic objects (e.g. 'Portfolios', 'Transactions').
    'Company' / 'Stock' is considered the built-in base object (no record here).
    """
    __tablename__ = "custom_objects"

    id:              Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    user_id:         Mapped[int]           = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name:            Mapped[str]           = mapped_column(String(100), nullable=False)  # API name e.g. "portfolio"
    label:           Mapped[str]           = mapped_column(String(100), nullable=False)               # UI label e.g. "Portfolio"
    plural_label:    Mapped[str]           = mapped_column(String(100), nullable=False)               # UI label e.g. "Portfolios"
    description:     Mapped[Optional[str]] = mapped_column(Text)
    created_at:      Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    fields: Mapped[List["CustomField"]] = relationship("CustomField", back_populates="custom_object", cascade="all, delete-orphan")
    records: Mapped[List["CustomRecord"]] = relationship("CustomRecord", back_populates="custom_object", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "user_id": self.user_id, "name": self.name, "label": self.label,
            "plural_label": self.plural_label, "description": self.description,
        }


class CustomField(Base):
    """
    User-defined fields. If object_id is null, it belongs to the built-in 'Stock' object.
    Otherwise, it belongs to a CustomObject.
    
    Types: number, percent, currency, text, formula, lookup
    """
    __tablename__ = "custom_fields"

    id:              Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    user_id:         Mapped[int]           = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    object_id:       Mapped[Optional[int]] = mapped_column(ForeignKey("custom_objects.id", ondelete="CASCADE")) # null = Stock
    name:            Mapped[str]           = mapped_column(String(100), nullable=False) # Must be unique per object
    label:           Mapped[str]           = mapped_column(String(200), nullable=False)
    field_type:      Mapped[str]           = mapped_column(String(20), default="text")  # number, percent, currency, text, formula, lookup
    
    # Only applicable if field_type == 'formula'
    formula:         Mapped[Optional[str]] = mapped_column(Text)
    
    # Only applicable if field_type == 'lookup'
    lookup_object:   Mapped[Optional[str]] = mapped_column(String(100)) # "stock" or custom object name
    
    description:     Mapped[Optional[str]] = mapped_column(Text)
    format_decimals: Mapped[int]           = mapped_column(Integer, default=2)
    display_order:   Mapped[int]           = mapped_column(Integer, default=0)
    is_active:       Mapped[bool]          = mapped_column(Boolean, default=True)
    created_at:      Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    custom_object: Mapped["CustomObject"] = relationship("CustomObject", back_populates="fields")
    
    __table_args__ = (
        UniqueConstraint("user_id", "object_id", "name", name="uq_user_object_fieldname"),
    )

    def __repr__(self) -> str:
        return f"<CustomField(name='{self.name}', type='{self.field_type}')>"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "user_id": self.user_id, "object_id": self.object_id, "name": self.name, "label": self.label,
            "field_type": self.field_type, "formula": self.formula, "lookup_object": self.lookup_object,
            "description": self.description, "format_decimals": self.format_decimals,
            "display_order": self.display_order, "is_active": self.is_active,
        }


class CustomRecord(Base):
    """
    Data rows for user-defined CustomObjects.
    The JSON payload stores field API names as keys.
    """
    __tablename__ = "custom_records"

    id:              Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    user_id:         Mapped[int]           = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    object_id:       Mapped[int]           = mapped_column(ForeignKey("custom_objects.id", ondelete="CASCADE"), nullable=False)
    name:            Mapped[str]           = mapped_column(String(255), nullable=False) # Standard 'Name' field all records have
    data:            Mapped[dict]          = mapped_column(JSON, default=dict) # The dynamic values
    created_at:      Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at:      Mapped[datetime]      = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    custom_object: Mapped["CustomObject"] = relationship("CustomObject", back_populates="records")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "user_id": self.user_id, "object_id": self.object_id, "name": self.name,
            "data": self.data, "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

# ══════════════════════════════════════════════════════════════════════════════
# MATH FORMULAS (Dynamic Equation Engine)
# ══════════════════════════════════════════════════════════════════════════════

class MathFormula(Base):
    """
    Stores mathematical equations used by the Valuation Engine.
    Users can override formulas from the UI.
    """
    __tablename__ = "math_formulas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text)
    formula_string: Mapped[str] = mapped_column(Text, nullable=False)
    available_variables: Mapped[str] = mapped_column(String(500), nullable=False)  # Comma-separated list of allowed variables
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "formula_string": self.formula_string,
            "available_variables": self.available_variables.split(",") if self.available_variables else [],
            "updated_at": self.updated_at.isoformat(),
        }

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════

def init_quant_db():
    """
    Create all ORM tables. Preserves existing legacy tables (tracked_stocks,
    stock_history, stock_cache) used by the Phase 1 dashboard.
    """
    Base.metadata.create_all(engine)
    
    # Seed default data providers
    session = SessionLocal()
    try:
        defaults = [
            {"name": "Yahoo Finance", "provider_type": "yahoo", "is_active": True},
            {"name": "Alpha Vantage", "provider_type": "alphavantage", "base_url": "https://www.alphavantage.co/query"},
            {"name": "Financial Modeling Prep", "provider_type": "fmp", "base_url": "https://financialmodelingprep.com/api/v3"},
        ]
        
        for d in defaults:
            if not session.query(DataProvider).filter_by(name=d["name"]).first():
                provider = DataProvider(**d)
                session.add(provider)
                
        # Seed default Math Formulas
        default_formulas = [
            {
                "key": "capm_expected_return",
                "name": "CAPM Expected Return",
                "description": "Calculates the required rate of return for an asset.",
                "formula_string": "rf + beta * erp",
                "available_variables": "rf,beta,erp"
            },
            {
                "key": "cost_of_debt",
                "name": "Cost of Debt",
                "description": "Estimates the cost of debt. Falls back to 50% of cost of equity if no debt.",
                "formula_string": "(abs(interest_expense) / total_debt) if total_debt > 0 else (cost_of_equity * 0.5)",
                "available_variables": "interest_expense,total_debt,cost_of_equity"
            },
            {
                "key": "wacc",
                "name": "Weighted Average Cost of Capital (WACC)",
                "description": "Blended cost of capital taking into account equity, debt, and corporate taxes.",
                "formula_string": "weight_equity * cost_of_equity + weight_debt * cost_of_debt * (1 - tax_rate)",
                "available_variables": "weight_equity,cost_of_equity,weight_debt,cost_of_debt,tax_rate"
            },
            {
                "key": "terminal_value",
                "name": "DCF Terminal Value",
                "description": "Gordon Growth Model to estimate value beyond the projection period.",
                "formula_string": "(last_fcf * (1 + tg)) / (wacc - tg)",
                "available_variables": "last_fcf,tg,wacc"
            },
            {
                "key": "altman_z",
                "name": "Altman Z-Score",
                "description": "Predicts bankruptcy risk based on 5 financial ratios.",
                "formula_string": "c1 * (working_capital / total_assets) + c2 * (retained_earnings / total_assets) + c3 * (ebit / total_assets) + c4 * (market_cap / total_liabilities) + c5 * (revenue / total_assets)",
                "available_variables": "c1,c2,c3,c4,c5,working_capital,retained_earnings,ebit,market_cap,total_liabilities,revenue,total_assets"
            }
        ]
        
        for f in default_formulas:
            if not session.query(MathFormula).filter_by(key=f["key"]).first():
                formula = MathFormula(**f)
                session.add(formula)
                
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Failed to seed defaults: {e}")
    finally:
        session.close()
        
    print("[models] Quant database schema initialized successfully.")


# ── Helper: Get or Create Company ─────────────────────────────────────────────

def get_or_create_company(session: Session, ticker: str, **kwargs) -> Company:
    """Get a Company by ticker, or create it if it doesn't exist."""
    ticker = ticker.upper().strip()
    company = session.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        company = Company(ticker=ticker, **kwargs)
        session.add(company)
        session.flush()
    else:
        # Update fields if provided
        for key, val in kwargs.items():
            if val is not None and hasattr(company, key):
                setattr(company, key, val)
        session.flush()
    return company


def get_latest_macro(session: Session) -> Optional[MacroRate]:
    """Get the most recent macro rate entry."""
    return session.query(MacroRate).order_by(MacroRate.date.desc()).first()


def get_latest_valuation(session: Session, company_id: int) -> Optional[Valuation]:
    """Get the most recent valuation for a company."""
    return session.query(Valuation).filter(
        Valuation.company_id == company_id
    ).order_by(Valuation.computed_at.desc()).first()


# ── Module Self-Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_quant_db()
    print(f"Database: {DB_PATH}")
    print("Tables:", Base.metadata.tables.keys())
