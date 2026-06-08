"""
screener.py — Quantitative Screener for Tőzsde Figyelő
Dynamic, multi-parameter segmentation engine for 1,000+ equities.

Supports complex, chained filtering via a fluent API:
    results = (QuantitativeScreener()
        .filter_sector(["Technology", "Healthcare"])
        .filter_z_score(min=2.9)
        .filter_mos(min=20.0)
        .filter_debt_to_equity(max=1.5)
        .sort_by("margin_of_safety_pct", descending=True)
        .limit(50)
        .execute())

Also supports raw dict-based filtering for API integration:
    results = QuantitativeScreener.from_params({
        "sector": "Technology",
        "min_mos": 20,
        "min_z": 2.9,
        "sort_by": "margin_of_safety_pct",
        "limit": 50,
    })
"""

from __future__ import annotations

from typing import Optional
import pandas as pd
from sqlalchemy import and_, or_, desc, asc, func
from sqlalchemy.orm import Session, Query

from models import (
    get_session, Company, QuarterlyFinancials, DailyPrice, Valuation,
    get_latest_valuation,
)


# ══════════════════════════════════════════════════════════════════════════════
# QUANTITATIVE SCREENER
# ══════════════════════════════════════════════════════════════════════════════

class QuantitativeScreener:
    """
    Dynamic, multi-parameter equity screener using SQLAlchemy ORM queries.

    Design:
        - Fluent API (chained method calls)
        - Joins Company → Valuation (latest) for filtering
        - Optionally joins QuarterlyFinancials for fundamental filters
        - Results returned as list[dict] or pandas DataFrame

    Performance:
        - Uses subqueries for "latest valuation per company" to avoid N+1
        - All filtering done at the SQL level (not in-memory)
    """

    def __init__(self, session: Optional[Session] = None):
        self._session = session or get_session()
        self._own_session = session is None  # Track if we created the session
        self._filters = []
        self._sort_column = "margin_of_safety_pct"
        self._sort_desc = True
        self._limit_val = 100
        self._offset_val = 0
        self._include_fundamentals = False
        self._decile_column: Optional[str] = None

    def __del__(self):
        if self._own_session and self._session:
            try:
                self._session.close()
            except Exception:
                pass

    # ── Fluent Filter Methods ─────────────────────────────────────────────────

    def filter_sector(self, sectors: str | list[str]) -> QuantitativeScreener:
        """Filter by one or more sectors."""
        if isinstance(sectors, str):
            sectors = [sectors]
        self._filters.append(("sector", sectors))
        return self

    def filter_industry(self, industries: str | list[str]) -> QuantitativeScreener:
        """Filter by one or more industries."""
        if isinstance(industries, str):
            industries = [industries]
        self._filters.append(("industry", industries))
        return self

    def filter_signal(self, signals: str | list[str]) -> QuantitativeScreener:
        """Filter by valuation signal (STRONG_BUY, BUY, HOLD, VALUE_TRAP, OVERVALUED)."""
        if isinstance(signals, str):
            signals = [signals]
        self._filters.append(("signal", signals))
        return self

    def filter_z_zone(self, zones: str | list[str]) -> QuantitativeScreener:
        """Filter by Z-score zone (SAFE, GREY, DISTRESS)."""
        if isinstance(zones, str):
            zones = [zones]
        self._filters.append(("z_zone", zones))
        return self

    def filter_z_score(self, min: Optional[float] = None, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by Altman Z-score range."""
        if min is not None:
            self._filters.append(("z_min", min))
        if max is not None:
            self._filters.append(("z_max", max))
        return self

    def filter_mos(self, min: Optional[float] = None, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by Margin of Safety percentage."""
        if min is not None:
            self._filters.append(("mos_min", min))
        if max is not None:
            self._filters.append(("mos_max", max))
        return self

    def filter_wacc(self, min: Optional[float] = None, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by WACC (as decimal, e.g. 0.10 for 10%)."""
        if min is not None:
            self._filters.append(("wacc_min", min))
        if max is not None:
            self._filters.append(("wacc_max", max))
        return self

    def filter_market_cap(self, min: Optional[float] = None, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by market capitalization."""
        if min is not None:
            self._filters.append(("mcap_min", min))
        if max is not None:
            self._filters.append(("mcap_max", max))
        return self

    def filter_beta(self, min: Optional[float] = None, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by company beta."""
        if min is not None:
            self._filters.append(("beta_min", min))
        if max is not None:
            self._filters.append(("beta_max", max))
        return self

    def filter_debt_to_equity(self, max: Optional[float] = None) -> QuantitativeScreener:
        """Filter by debt-to-equity ratio (requires fundamentals join)."""
        if max is not None:
            self._filters.append(("dte_max", max))
            self._include_fundamentals = True
        return self

    def filter_fcf_yield(self, min: Optional[float] = None) -> QuantitativeScreener:
        """Filter by free cash flow yield (FCF / Market Cap)."""
        if min is not None:
            self._filters.append(("fcf_yield_min", min))
            self._include_fundamentals = True
        return self

    def filter_data_quality(self, quality: str | list[str]) -> QuantitativeScreener:
        """Filter by data quality (FULL, ESTIMATED, PARTIAL)."""
        if isinstance(quality, str):
            quality = [quality]
        self._filters.append(("data_quality", quality))
        return self

    # ── Sorting & Pagination ──────────────────────────────────────────────────

    def sort_by(self, column: str, descending: bool = True) -> QuantitativeScreener:
        """Sort results by column name."""
        self._sort_column = column
        self._sort_desc = descending
        return self

    def limit(self, n: int) -> QuantitativeScreener:
        """Limit number of results."""
        self._limit_val = n
        return self

    def offset(self, n: int) -> QuantitativeScreener:
        """Offset for pagination."""
        self._offset_val = n
        return self

    # ── Segmentation ──────────────────────────────────────────────────────────

    def top_decile(self, column: str) -> QuantitativeScreener:
        """
        Return the top decile (top 10%) ranked by the given column.
        Example: screener.top_decile("margin_of_safety_pct")
        """
        self._decile_column = column
        return self

    def segment_by_industry(self, rank_by: str = "margin_of_safety_pct") -> list[dict]:
        """
        Segment all firms by Industry, rank within each group.
        Returns grouped results.
        """
        all_results = self._build_and_execute()
        df = pd.DataFrame(all_results)

        if df.empty or "industry" not in df.columns or rank_by not in df.columns:
            return []

        # Rank within each industry
        df["industry_rank"] = df.groupby("industry")[rank_by].rank(
            ascending=False, method="dense"
        )
        df = df.sort_values(["industry", "industry_rank"])

        return df.to_dict("records")

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self) -> list[dict]:
        """Execute the screener and return results as list[dict]."""
        results = self._build_and_execute()

        # Apply top decile if requested
        if self._decile_column and results:
            df = pd.DataFrame(results)
            if self._decile_column in df.columns:
                threshold = df[self._decile_column].quantile(0.90)
                df = df[df[self._decile_column] >= threshold]
                results = df.to_dict("records")

        return results

    def execute_df(self) -> pd.DataFrame:
        """Execute the screener and return results as a pandas DataFrame."""
        return pd.DataFrame(self.execute())

    def _build_and_execute(self) -> list[dict]:
        """Build SQLAlchemy query from accumulated filters and execute."""
        session = self._session

        # Subquery: latest valuation ID per company
        latest_val_sq = (
            session.query(
                Valuation.company_id,
                func.max(Valuation.computed_at).label("max_computed")
            )
            .group_by(Valuation.company_id)
            .subquery("latest_val")
        )

        # Main query: Company + latest Valuation
        query = (
            session.query(Company, Valuation)
            .join(Valuation, Company.id == Valuation.company_id)
            .join(
                latest_val_sq,
                and_(
                    Valuation.company_id == latest_val_sq.c.company_id,
                    Valuation.computed_at == latest_val_sq.c.max_computed,
                )
            )
            .filter(Company.is_active == True)
        )

        # Optionally join fundamentals
        latest_qf = None
        if self._include_fundamentals:
            latest_qf_sq = (
                session.query(
                    QuarterlyFinancials.company_id,
                    func.max(QuarterlyFinancials.period_end).label("max_period")
                )
                .group_by(QuarterlyFinancials.company_id)
                .subquery("latest_qf")
            )
            query = query.outerjoin(
                QuarterlyFinancials,
                and_(
                    QuarterlyFinancials.company_id == Company.id,
                    QuarterlyFinancials.period_end == latest_qf_sq.c.max_period,
                )
            ).outerjoin(latest_qf_sq, Company.id == latest_qf_sq.c.company_id)

        # ── Apply filters ──
        for filter_type, value in self._filters:
            if filter_type == "sector":
                query = query.filter(Company.sector.in_(value))
            elif filter_type == "industry":
                query = query.filter(Company.industry.in_(value))
            elif filter_type == "signal":
                query = query.filter(Valuation.signal.in_(value))
            elif filter_type == "z_zone":
                query = query.filter(Valuation.z_score_zone.in_(value))
            elif filter_type == "z_min":
                query = query.filter(Valuation.altman_z_score >= value)
            elif filter_type == "z_max":
                query = query.filter(Valuation.altman_z_score <= value)
            elif filter_type == "mos_min":
                query = query.filter(Valuation.margin_of_safety_pct >= value)
            elif filter_type == "mos_max":
                query = query.filter(Valuation.margin_of_safety_pct <= value)
            elif filter_type == "wacc_min":
                query = query.filter(Valuation.wacc >= value)
            elif filter_type == "wacc_max":
                query = query.filter(Valuation.wacc <= value)
            elif filter_type == "mcap_min":
                query = query.filter(Company.market_cap >= value)
            elif filter_type == "mcap_max":
                query = query.filter(Company.market_cap <= value)
            elif filter_type == "beta_min":
                query = query.filter(Company.beta >= value)
            elif filter_type == "beta_max":
                query = query.filter(Company.beta <= value)
            elif filter_type == "data_quality":
                query = query.filter(Valuation.data_quality.in_(value))
            elif filter_type == "dte_max" and self._include_fundamentals:
                # D/E = total_debt / total_equity
                query = query.filter(
                    QuarterlyFinancials.total_debt / func.nullif(QuarterlyFinancials.total_equity, 0) <= value
                )
            elif filter_type == "fcf_yield_min" and self._include_fundamentals:
                # FCF Yield = FCF / Market Cap
                query = query.filter(
                    QuarterlyFinancials.free_cash_flow / func.nullif(Company.market_cap, 0) >= value
                )

        # ── Sorting ──
        sort_col = self._resolve_sort_column(self._sort_column)
        if sort_col is not None:
            query = query.order_by(desc(sort_col) if self._sort_desc else asc(sort_col))

        # ── Pagination ──
        query = query.offset(self._offset_val).limit(self._limit_val)

        # ── Execute and format ──
        rows = query.all()
        results = []
        for company, valuation in rows:
            result = {
                "ticker": company.ticker,
                "name": company.name,
                "sector": company.sector,
                "industry": company.industry,
                "country": company.country,
                "market_cap": company.market_cap,
                "beta": company.beta,
                "signal": valuation.signal,
                "signal_reason": valuation.signal_reason,
                "intrinsic_value": valuation.intrinsic_value_dcf,
                "market_price": valuation.market_price,
                "margin_of_safety_pct": valuation.margin_of_safety_pct,
                "altman_z_score": valuation.altman_z_score,
                "z_score_zone": valuation.z_score_zone,
                "wacc": valuation.wacc,
                "capm_expected_return": valuation.capm_expected_return,
                "data_quality": valuation.data_quality,
                "computed_at": valuation.computed_at.isoformat() if valuation.computed_at else None,
            }
            results.append(result)

        return results

    def _resolve_sort_column(self, col_name: str):
        """Map sort column name to SQLAlchemy column object."""
        valuation_cols = {
            "margin_of_safety_pct": Valuation.margin_of_safety_pct,
            "altman_z_score": Valuation.altman_z_score,
            "intrinsic_value_dcf": Valuation.intrinsic_value_dcf,
            "wacc": Valuation.wacc,
            "capm_expected_return": Valuation.capm_expected_return,
            "market_price": Valuation.market_price,
            "signal": Valuation.signal,
        }
        company_cols = {
            "ticker": Company.ticker,
            "name": Company.name,
            "sector": Company.sector,
            "market_cap": Company.market_cap,
            "beta": Company.beta,
        }
        return valuation_cols.get(col_name) or company_cols.get(col_name)

    # ── Class Method: From API Parameters ─────────────────────────────────────

    @classmethod
    def from_params(cls, params: dict) -> list[dict]:
        """
        Create a screener from a flat dict of parameters (for API integration).

        Supported params:
            sector, industry, signal, z_zone, min_mos, max_mos,
            min_z, max_z, min_wacc, max_wacc, min_mcap, max_mcap,
            min_beta, max_beta, max_dte, min_fcf_yield,
            data_quality, sort_by, descending, limit, offset,
            top_decile
        """
        screener = cls()

        if params.get("sector"):
            sectors = params["sector"] if isinstance(params["sector"], list) else [params["sector"]]
            screener.filter_sector(sectors)

        if params.get("industry"):
            industries = params["industry"] if isinstance(params["industry"], list) else [params["industry"]]
            screener.filter_industry(industries)

        if params.get("signal"):
            signals = params["signal"] if isinstance(params["signal"], list) else [params["signal"]]
            screener.filter_signal(signals)

        if params.get("z_zone"):
            zones = params["z_zone"] if isinstance(params["z_zone"], list) else [params["z_zone"]]
            screener.filter_z_zone(zones)

        if params.get("min_mos") is not None:
            screener.filter_mos(min=float(params["min_mos"]))
        if params.get("max_mos") is not None:
            screener.filter_mos(max=float(params["max_mos"]))

        if params.get("min_z") is not None:
            screener.filter_z_score(min=float(params["min_z"]))
        if params.get("max_z") is not None:
            screener.filter_z_score(max=float(params["max_z"]))

        if params.get("min_wacc") is not None:
            screener.filter_wacc(min=float(params["min_wacc"]))
        if params.get("max_wacc") is not None:
            screener.filter_wacc(max=float(params["max_wacc"]) / 100.0)  # API sends as percentage

        if params.get("min_mcap") is not None:
            screener.filter_market_cap(min=float(params["min_mcap"]))
        if params.get("max_mcap") is not None:
            screener.filter_market_cap(max=float(params["max_mcap"]))

        if params.get("max_dte") is not None:
            screener.filter_debt_to_equity(max=float(params["max_dte"]))

        if params.get("min_fcf_yield") is not None:
            screener.filter_fcf_yield(min=float(params["min_fcf_yield"]))

        if params.get("data_quality"):
            screener.filter_data_quality(params["data_quality"])

        sort_by = params.get("sort_by", "margin_of_safety_pct")
        descending = params.get("descending", True)
        if isinstance(descending, str):
            descending = descending.lower() not in ("false", "0", "no")
        screener.sort_by(sort_by, descending=descending)

        if params.get("limit"):
            screener.limit(int(params["limit"]))
        if params.get("offset"):
            screener.offset(int(params["offset"]))

        if params.get("top_decile"):
            screener.top_decile(params["top_decile"])

        return screener.execute()


# ── Module Self-Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing Quantitative Screener...")

    # Example 1: All stocks with positive MoS and Safe Z
    results = (QuantitativeScreener()
        .filter_mos(min=0)
        .filter_z_zone("SAFE")
        .sort_by("margin_of_safety_pct", descending=True)
        .limit(20)
        .execute())

    print(f"\nSafe stocks with positive MoS: {len(results)}")
    for r in results[:5]:
        print(f"  {r['ticker']:6s} | MoS={r['margin_of_safety_pct']:>6.1f}% | Z={r['altman_z_score']:.2f} | {r['signal']}")

    # Example 2: From API params
    results2 = QuantitativeScreener.from_params({
        "sector": "Technology",
        "min_mos": 10,
        "sort_by": "altman_z_score",
        "limit": 10,
    })
    print(f"\nTech stocks with MoS>10%: {len(results2)}")
