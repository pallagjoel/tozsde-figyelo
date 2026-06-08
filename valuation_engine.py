"""
valuation_engine.py — Core Valuation Mathematics for Tőzsde Figyelő
Runs EXCLUSIVELY against the local SQLite database. Zero external API calls.
All parameters dynamically inherited from config.yaml via MathConfig.

Models implemented:
    1. CAPM Expected Return:  E(Ri) = Rf + β × (Rm − Rf)
    2. WACC Estimation:       WACC = (E/V)×Re + (D/V)×Rd×(1−Tc)
    3. DCF (2-Stage, 10-Year): Discounted Free Cash Flow with Gordon Growth Terminal
    4. Altman Z-Score:         Z = c1×X1 + c2×X2 + c3×X3 + c4×X4 + c5×X5
    5. Signal Generation:      Value Trap protection via Z-score override
    6. Scenario Sweep:         Bear/Base/Bull across entire database
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    Company, QuarterlyFinancials, DailyPrice, Valuation, MacroRate,
    get_session, get_latest_macro, get_or_create_company,
)
from math_config import get_config, MathConfig, ScenarioConfig

# ── Config-driven defaults (no hardcoded values) ─────────────────────────────
# All variables are now read from config.yaml via MathConfig.
# These module-level references exist only for backward compatibility.

def _cfg():
    return get_config()


# ══════════════════════════════════════════════════════════════════════════════
# SAFE MATH UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def safe_div(numerator: Optional[float], denominator: Optional[float], default: float = 0.0) -> float:
    """Division with None/zero protection."""
    if numerator is None or denominator is None or denominator == 0:
        return default
    return numerator / denominator


def safe_float(value, default: float = 0.0) -> float:
    """Safely convert to float."""
    if value is None:
        return default
    try:
        v = float(value)
        return default if np.isnan(v) or np.isinf(v) else v
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# 1. CAPM EXPECTED RETURN
# ══════════════════════════════════════════════════════════════════════════════

def compute_capm(
    beta: float,
    risk_free_rate: Optional[float] = None,
    market_return: Optional[float] = None,
    erp_override: Optional[float] = None,
    scenario: Optional[ScenarioConfig] = None,
) -> dict:
    """
    Capital Asset Pricing Model.
    E(Ri) = Rf + β × (Rm − Rf)

    All parameters dynamically sourced from MathConfig or scenario overrides.
    """
    cfg = _cfg()
    sc = scenario

    rf = risk_free_rate if risk_free_rate is not None else (
        sc.risk_free_rate if sc else cfg.get("risk_free_rate", default=0.0425)
    )
    rm = market_return if market_return is not None else (
        sc.market_return if sc else cfg.get("market_return", default=0.10)
    )
    erp = erp_override if erp_override is not None else (
        sc.equity_risk_premium if sc else cfg.get("equity_risk_premium", default=0.0575)
    )

    expected_return = rf + beta * erp

    return {
        "capm_expected_return": round(expected_return, 6),
        "risk_free_rate":       round(rf, 6),
        "equity_risk_premium":  round(erp, 6),
        "beta_used":            round(beta, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. WACC ESTIMATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_wacc(
    market_cap: float,
    total_debt: float,
    cost_of_equity: float,
    interest_expense: Optional[float] = None,
    tax_rate: Optional[float] = None,
    scenario: Optional[ScenarioConfig] = None,
) -> dict:
    """
    Weighted Average Cost of Capital.
    WACC = (E/V) × Re + (D/V) × Rd × (1 − Tc)

    All bounds dynamically sourced from MathConfig.
    """
    cfg = _cfg()
    sc = scenario

    tax = tax_rate if tax_rate is not None else (
        sc.corporate_tax_rate if sc else cfg.get("corporate_tax_rate", default=0.21)
    )
    min_wacc = sc.min_wacc if sc else cfg.get("min_wacc", default=0.06)
    max_wacc = sc.max_wacc if sc else cfg.get("max_wacc", default=0.25)
    wacc_adj = sc.wacc_adjustment if sc else 0.0

    total_debt = safe_float(total_debt, 0.0)
    market_cap = safe_float(market_cap, 1.0)

    total_value = market_cap + total_debt
    weight_equity = market_cap / total_value if total_value > 0 else 1.0
    weight_debt   = total_debt / total_value if total_value > 0 else 0.0

    # Estimate cost of debt from interest expense / total debt, or use risk-free + spread
    if interest_expense and total_debt > 0:
        cost_of_debt = abs(interest_expense) / total_debt
    else:
        cost_of_debt = cost_of_equity * 0.5  # rough approximation

    # Clamp cost_of_debt to reasonable range
    cost_of_debt = max(0.01, min(cost_of_debt, 0.20))

    wacc = weight_equity * cost_of_equity + weight_debt * cost_of_debt * (1 - tax)
    wacc += wacc_adj  # Apply scenario adjustment

    # Clamp WACC to config-driven range
    wacc = max(min_wacc, min(wacc, max_wacc))

    return {
        "wacc":           round(wacc, 6),
        "cost_of_equity": round(cost_of_equity, 6),
        "cost_of_debt":   round(cost_of_debt, 6),
        "weight_equity":  round(weight_equity, 4),
        "weight_debt":    round(weight_debt, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. DCF MODEL (2-STAGE, 10-YEAR)
# ══════════════════════════════════════════════════════════════════════════════

def compute_dcf(
    recent_fcfs: list[float],
    wacc: float,
    shares_outstanding: float,
    total_debt: float,
    cash: float,
    terminal_growth: Optional[float] = None,
    sector: Optional[str] = None,
    scenario: Optional[ScenarioConfig] = None,
) -> dict:
    """
    Discounted Cash Flow — 2-stage model.

    Stage 1 (Years 1–5): FCF grows at historical CAGR (capped by config).
    Stage 2 (Years 6–10): Growth decays linearly toward terminal rate.
    Terminal Value: Gordon Growth Model.

    All caps, floors, and growth parameters driven by MathConfig/scenario.
    """
    cfg = _cfg()
    sc = scenario

    # Resolve config-driven parameters
    tg = terminal_growth if terminal_growth is not None else (
        sc.terminal_growth_rate if sc else cfg.get("terminal_growth_rate", sector=sector, default=0.025)
    )
    growth_cap = sc.max_fcf_growth_cap if sc else cfg.get("max_fcf_growth_cap", sector=sector, default=0.25)
    growth_floor = sc.min_fcf_growth_floor if sc else cfg.get("min_fcf_growth_floor", sector=sector, default=-0.10)
    proj_years = sc.dcf_projection_years if sc else cfg.get("dcf_projection_years", default=10)
    fcf_mult = sc.fcf_growth_multiplier if sc else 1.0

    shares = safe_float(shares_outstanding, 1.0)
    debt   = safe_float(total_debt, 0.0)
    cash_v = safe_float(cash, 0.0)

    # Filter valid (non-None, non-zero) FCFs
    valid_fcfs = [f for f in recent_fcfs if f is not None and f != 0]

    if len(valid_fcfs) < 1:
        return {"error": "No valid free cash flow data available."}

    # Use the most recent FCF as base (annualized if quarterly)
    base_fcf = valid_fcfs[0]

    # If we have quarterly data, annualize by summing last 4 quarters
    if len(valid_fcfs) >= 4:
        base_fcf = sum(valid_fcfs[:4])  # Sum of most recent 4 quarters
    elif len(valid_fcfs) >= 2:
        # Annualize from available quarters
        base_fcf = valid_fcfs[0] * (4 / len(valid_fcfs))

    if base_fcf <= 0:
        # Handle negative FCF: use average of positive quarters if any
        positives = [f for f in valid_fcfs if f > 0]
        if positives:
            base_fcf = sum(positives) / len(positives) * 4
        else:
            return {
                "error": "All free cash flows are negative — DCF not applicable.",
                "negative_fcf_flag": True,
            }

    # ── Compute historical growth rate ──
    if len(valid_fcfs) >= 8:
        old_annual = sum(valid_fcfs[-4:])
        new_annual = sum(valid_fcfs[:4])
        if old_annual > 0 and new_annual > 0:
            years = len(valid_fcfs) / 4
            growth_rate = (new_annual / old_annual) ** (1 / max(years, 1)) - 1
        else:
            growth_rate = 0.05
    elif len(valid_fcfs) >= 2:
        growth_rate = (valid_fcfs[0] / valid_fcfs[-1]) ** (1 / max(len(valid_fcfs) - 1, 1)) - 1
    else:
        growth_rate = 0.05

    # Apply scenario growth multiplier and cap
    growth_rate *= fcf_mult
    growth_rate = max(growth_floor, min(growth_rate, growth_cap))

    # ── Project FCFs ──
    projected_fcfs = []
    for year in range(1, proj_years + 1):
        if year <= 5:
            rate = growth_rate
        else:
            decay_frac = (year - 5) / 5
            rate = growth_rate * (1 - decay_frac) + tg * decay_frac

        fcf = base_fcf * (1 + rate) ** year
        projected_fcfs.append(fcf)

    # ── Discount projected FCFs ──
    pv_fcfs = sum(fcf / (1 + wacc) ** (i + 1) for i, fcf in enumerate(projected_fcfs))

    # ── Terminal Value (Gordon Growth) ──
    last_fcf = projected_fcfs[-1]
    if wacc <= tg:
        tg = wacc * 0.5  # Safety: terminal growth can't exceed WACC

    tv = (last_fcf * (1 + tg)) / (wacc - tg)
    pv_tv = tv / (1 + wacc) ** proj_years

    # ── Enterprise Value → Equity Value → Per-Share Value ──
    enterprise_value = pv_fcfs + pv_tv
    net_debt = debt - cash_v
    equity_value = enterprise_value - net_debt
    intrinsic_per_share = equity_value / shares if shares > 0 else 0.0

    return {
        "intrinsic_value_dcf":  round(max(intrinsic_per_share, 0), 4),
        "base_fcf":             round(base_fcf, 2),
        "fcf_growth_rate":      round(growth_rate, 6),
        "terminal_growth_rate": round(tg, 6),
        "projected_fcfs":       [round(f, 2) for f in projected_fcfs],
        "pv_of_fcfs":           round(pv_fcfs, 2),
        "terminal_value":       round(tv, 2),
        "pv_of_terminal":       round(pv_tv, 2),
        "enterprise_value":     round(enterprise_value, 2),
        "net_debt":             round(net_debt, 2),
        "equity_value":         round(equity_value, 2),
        "shares_outstanding":   round(shares, 0),
        "negative_fcf_flag":    False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. ALTMAN Z-SCORE
# ══════════════════════════════════════════════════════════════════════════════

def compute_altman_z(
    working_capital: float,
    retained_earnings: float,
    ebit: float,
    market_cap: float,
    total_liabilities: float,
    revenue: float,
    total_assets: float,
    sector: Optional[str] = None,
    scenario: Optional[ScenarioConfig] = None,
) -> dict:
    """
    Altman Z-Score with configurable coefficients.
    Z = c1×X1 + c2×X2 + c3×X3 + c4×X4 + c5×X5

    Coefficients and thresholds are dynamically sourced from config.yaml,
    allowing sector-specific models (e.g., financials) and custom stress-testing.
    """
    cfg = _cfg()
    sc = scenario

    # Get coefficients (sector-specific if available)
    coeff = cfg.get_z_coefficients(sector=sector)
    c1 = coeff.get("x1_wc_ta", 1.2)
    c2 = coeff.get("x2_re_ta", 1.4)
    c3 = coeff.get("x3_ebit_ta", 3.3)
    c4 = coeff.get("x4_mcap_tl", 0.6)
    c5 = coeff.get("x5_rev_ta", 1.0)

    # Get thresholds (scenario-specific if available)
    z_safe = sc.z_safe_threshold if sc else cfg.get("z_safe_threshold", default=2.99)
    z_grey = sc.z_grey_threshold if sc else cfg.get("z_grey_threshold", default=1.81)

    ta = safe_float(total_assets, 1.0)

    x1 = safe_div(working_capital, ta)
    x2 = safe_div(retained_earnings, ta)
    x3 = safe_div(ebit, ta)
    x4 = safe_div(market_cap, total_liabilities)
    x5 = safe_div(revenue, ta)

    z = c1 * x1 + c2 * x2 + c3 * x3 + c4 * x4 + c5 * x5

    if z > z_safe:
        zone = "SAFE"
    elif z > z_grey:
        zone = "GREY"
    else:
        zone = "DISTRESS"

    return {
        "altman_z_score": round(z, 4),
        "z_score_zone":   zone,
        "z_x1":           round(x1, 6),
        "z_x2":           round(x2, 6),
        "z_x3":           round(x3, 6),
        "z_x4":           round(x4, 6),
        "z_x5":           round(x5, 6),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. SIGNAL GENERATION (with Value Trap Protection)
# ══════════════════════════════════════════════════════════════════════════════

def generate_signal(
    margin_of_safety: float,
    z_score: float,
    z_zone: str,
    revenue_decline: bool = False,
    negative_fcf: bool = False,
    scenario: Optional[ScenarioConfig] = None,
) -> dict:
    """
    Generate buy/sell/hold signal with Value Trap protection.
    MoS thresholds driven by config.yaml / scenario overrides.
    """
    cfg = _cfg()
    sc = scenario

    mos_strong = sc.mos_strong_buy if sc else cfg.get("mos_strong_buy", default=30.0)
    mos_buy = sc.mos_buy if sc else cfg.get("mos_buy", default=20.0)
    z_grey_th = sc.z_grey_threshold if sc else cfg.get("z_grey_threshold", default=1.81)

    if margin_of_safety is None or z_score is None:
        return {"signal": "INSUFFICIENT_DATA", "signal_reason": "Missing valuation data for signal generation."}

    scenario_label = f" [{sc.label}]" if sc else ""

    # ── Value Trap Protection: structural failure overrides ──
    if z_zone == "DISTRESS" and margin_of_safety >= mos_buy:
        return {
            "signal": "VALUE_TRAP",
            "signal_reason": (
                f"⚠️ VALUE TRAP{scenario_label}: Stock appears cheap (MoS={margin_of_safety:.1f}%) "
                f"but Altman Z={z_score:.2f} indicates DISTRESS (bankruptcy risk). "
                f"Negative Z-score overrides the cheap price signal."
            ),
        }

    if revenue_decline and margin_of_safety >= mos_buy:
        return {
            "signal": "VALUE_TRAP",
            "signal_reason": (
                f"⚠️ VALUE TRAP{scenario_label}: Stock appears cheap (MoS={margin_of_safety:.1f}%) "
                f"but revenue declined >50% QoQ — structural break detected."
            ),
        }

    if negative_fcf:
        return {
            "signal": "VALUE_TRAP",
            "signal_reason": (
                f"⚠️ VALUE TRAP{scenario_label}: All recent quarters show negative free cash flow. "
                f"DCF model may be unreliable."
            ),
        }

    # ── Standard signal logic (config-driven thresholds) ──
    if margin_of_safety >= mos_strong and z_zone == "SAFE":
        return {
            "signal": "STRONG_BUY",
            "signal_reason": (
                f"🟢 STRONG BUY{scenario_label}: MoS={margin_of_safety:.1f}% (≥{mos_strong}%) "
                f"with Z={z_score:.2f} (SAFE zone). Significant undervaluation with strong financial health."
            ),
        }

    if margin_of_safety >= mos_buy and z_score > z_grey_th:
        return {
            "signal": "BUY",
            "signal_reason": (
                f"🟢 BUY{scenario_label}: MoS={margin_of_safety:.1f}% (≥{mos_buy}%) with Z={z_score:.2f} "
                f"({'SAFE' if z_zone == 'SAFE' else 'GREY'} zone). Undervalued with acceptable risk."
            ),
        }

    if margin_of_safety > 0:
        return {
            "signal": "HOLD",
            "signal_reason": (
                f"🟡 HOLD{scenario_label}: MoS={margin_of_safety:.1f}% — some upside potential but "
                f"insufficient margin for a buy signal. Z={z_score:.2f} ({z_zone})."
            ),
        }

    return {
        "signal": "OVERVALUED",
        "signal_reason": (
            f"🔴 OVERVALUED{scenario_label}: MoS={margin_of_safety:.1f}% — market price exceeds "
            f"estimated intrinsic value. Z={z_score:.2f} ({z_zone})."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR: Run Full Valuation for One Company
# ══════════════════════════════════════════════════════════════════════════════

def run_valuation_for_company(
    session: Session,
    company: Company,
    scenario_name: Optional[str] = None,
) -> Optional[Valuation]:
    """
    Run the complete valuation pipeline for a single company.
    All data sourced from the local database.
    All parameters dynamically inherited from MathConfig.

    Args:
        session: SQLAlchemy session
        company: Company ORM object
        scenario_name: Optional scenario ("bear", "base", "bull", "deep_value")
    """
    cfg = _cfg()
    ticker = company.ticker
    sector = company.sector

    # Resolve scenario config
    sc = cfg.scenario(scenario_name or "base", sector=sector) if scenario_name else None

    # ── Step 1: Macro Rates ──
    macro = get_latest_macro(session)
    if macro and macro.risk_free_rate is not None:
        rf = macro.risk_free_rate / 100.0
        rm = (macro.market_return or cfg.get("market_return", default=0.10) * 100) / 100.0
        inflation = (macro.inflation_rate or 2.5) / 100.0
    else:
        rf = sc.risk_free_rate if sc else cfg.get("risk_free_rate", default=0.0425)
        rm = sc.market_return if sc else cfg.get("market_return", default=0.10)
        inflation = 0.025

    # ── Step 2: Quarterly Financials (most recent quarters) ──
    financials = session.query(QuarterlyFinancials).filter(
        QuarterlyFinancials.company_id == company.id
    ).order_by(QuarterlyFinancials.period_end.desc()).limit(12).all()

    if not financials:
        print(f"  [{ticker}] ⚠ No quarterly financials — skipping valuation.")
        val = Valuation(
            company_id=company.id,
            signal="INSUFFICIENT_DATA",
            signal_reason="No quarterly financial data available.",
            data_quality="MISSING",
        )
        session.add(val)
        return val

    latest_q = financials[0]

    # ── Step 3: Latest Market Price ──
    latest_price_row = session.query(DailyPrice).filter(
        DailyPrice.company_id == company.id
    ).order_by(DailyPrice.date.desc()).first()

    market_price = latest_price_row.close if latest_price_row else None
    if not market_price:
        market_price = safe_float(company.market_cap) / safe_float(company.shares_outstanding, 1.0)

    # ── Step 4a: CAPM (config/scenario driven) ──
    beta = safe_float(company.beta, 1.0)
    capm = compute_capm(beta, risk_free_rate=rf, market_return=rm, scenario=sc)

    # ── Step 4b: WACC (config/scenario driven) ──
    market_cap = safe_float(company.market_cap, 0.0)
    total_debt = safe_float(latest_q.total_debt, 0.0)
    interest_exp = latest_q.interest_expense

    if interest_exp is not None:
        interest_exp = abs(interest_exp) * 4

    wacc_result = compute_wacc(
        market_cap=market_cap,
        total_debt=total_debt,
        cost_of_equity=capm["capm_expected_return"],
        interest_expense=interest_exp,
        scenario=sc,
    )

    # ── Step 4c: DCF (config/scenario/sector driven) ──
    fcfs = [safe_float(q.free_cash_flow) for q in financials if q.free_cash_flow is not None]
    tg_default = cfg.get("terminal_growth_rate", sector=sector, default=0.025)

    dcf_result = compute_dcf(
        recent_fcfs=fcfs,
        wacc=wacc_result["wacc"],
        shares_outstanding=safe_float(company.shares_outstanding, 1.0),
        total_debt=total_debt,
        cash=safe_float(latest_q.cash_and_equivalents, 0.0),
        terminal_growth=max(inflation, tg_default),
        sector=sector,
        scenario=sc,
    )

    dcf_error = dcf_result.get("error")
    negative_fcf = dcf_result.get("negative_fcf_flag", False)

    # ── Step 4d: Altman Z-Score ──
    # Use TTM (trailing twelve months) by summing recent 4 quarters for income items
    ttm_revenue = sum(safe_float(q.revenue) for q in financials[:4])
    ttm_ebit    = sum(safe_float(q.ebit or q.operating_income) for q in financials[:4])

    z_result = compute_altman_z(
        working_capital=safe_float(latest_q.working_capital, 0.0),
        retained_earnings=safe_float(latest_q.retained_earnings, 0.0),
        ebit=ttm_ebit,
        market_cap=market_cap,
        total_liabilities=safe_float(latest_q.total_liabilities, 1.0),
        revenue=ttm_revenue,
        total_assets=safe_float(latest_q.total_assets, 1.0),
        sector=sector,
        scenario=sc,
    )

    # ── Step 4e: Margin of Safety ──
    intrinsic = dcf_result.get("intrinsic_value_dcf")
    if intrinsic and intrinsic > 0 and market_price and market_price > 0:
        mos = (intrinsic - market_price) / intrinsic * 100
    else:
        mos = None

    # ── Step 4f: Revenue Decline Detection ──
    revenue_decline = False
    if len(financials) >= 2:
        rev_curr = safe_float(financials[0].revenue)
        rev_prev = safe_float(financials[1].revenue)
        if rev_prev > 0 and rev_curr > 0:
            decline_pct = (rev_curr - rev_prev) / rev_prev
            revenue_decline = decline_pct < -0.50  # >50% decline

    # ── Step 4g: Signal ──
    if dcf_error:
        sig = {
            "signal": "INSUFFICIENT_DATA" if not negative_fcf else "VALUE_TRAP",
            "signal_reason": dcf_error,
        }
    else:
        sig = generate_signal(
            margin_of_safety=mos,
            z_score=z_result["altman_z_score"],
            z_zone=z_result["z_score_zone"],
            revenue_decline=revenue_decline,
            negative_fcf=negative_fcf,
            scenario=sc,
        )

    # ── Step 5: Create Valuation Record ──
    data_quality = "FULL"
    if any(q.data_source == "yfinance" for q in financials[:4]):
        data_quality = "ESTIMATED"
    if len(financials) < 4:
        data_quality = "PARTIAL"

    val = Valuation(
        company_id=company.id,
        intrinsic_value_dcf=dcf_result.get("intrinsic_value_dcf"),
        market_price=round(market_price, 4) if market_price else None,
        margin_of_safety_pct=round(mos, 2) if mos is not None else None,
        fcf_growth_rate=dcf_result.get("fcf_growth_rate"),
        terminal_growth_rate=dcf_result.get("terminal_growth_rate"),
        pv_of_fcfs=dcf_result.get("pv_of_fcfs"),
        terminal_value=dcf_result.get("terminal_value"),
        pv_of_terminal=dcf_result.get("pv_of_terminal"),
        enterprise_value=dcf_result.get("enterprise_value"),
        net_debt=dcf_result.get("net_debt"),
        equity_value=dcf_result.get("equity_value"),
        capm_expected_return=capm["capm_expected_return"],
        risk_free_rate=capm["risk_free_rate"],
        equity_risk_premium=capm["equity_risk_premium"],
        beta_used=capm["beta_used"],
        wacc=wacc_result["wacc"],
        cost_of_equity=wacc_result["cost_of_equity"],
        cost_of_debt=wacc_result["cost_of_debt"],
        altman_z_score=z_result["altman_z_score"],
        z_score_zone=z_result["z_score_zone"],
        z_x1=z_result["z_x1"], z_x2=z_result["z_x2"], z_x3=z_result["z_x3"],
        z_x4=z_result["z_x4"], z_x5=z_result["z_x5"],
        signal=sig["signal"],
        signal_reason=sig["signal_reason"],
        revenue_decline_flag=revenue_decline,
        negative_fcf_flag=negative_fcf,
        data_quality=data_quality,
    )
    session.add(val)
    return val


# ══════════════════════════════════════════════════════════════════════════════
# BATCH ORCHESTRATOR: Run Valuations for All Tracked Companies
# ══════════════════════════════════════════════════════════════════════════════

def run_all_valuations() -> dict:
    """
    Phase B: The Valuation Engine.
    Iterates all active companies and computes DCF + Z-Score + CAPM + Signal.
    Runs exclusively against local data.
    """
    session = get_session()
    results = {"computed": [], "errors": [], "skipped": []}

    try:
        companies = session.query(Company).filter(Company.is_active == True).all()
        print(f"\n{'='*60}")
        print(f"  VALUATION ENGINE — Processing {len(companies)} companies")
        print(f"{'='*60}")

        for company in companies:
            try:
                print(f"\n  [{company.ticker}] Computing valuation...")
                val = run_valuation_for_company(session, company)

                if val:
                    results["computed"].append({
                        "ticker": company.ticker,
                        "signal": val.signal,
                        "intrinsic": val.intrinsic_value_dcf,
                        "market": val.market_price,
                        "mos": val.margin_of_safety_pct,
                        "z_score": val.altman_z_score,
                        "z_zone": val.z_score_zone,
                    })
                    print(f"  [{company.ticker}] ✓ Signal={val.signal} | "
                          f"Intrinsic=${val.intrinsic_value_dcf or 0:.2f} | "
                          f"Market=${val.market_price or 0:.2f} | "
                          f"MoS={val.margin_of_safety_pct or 0:.1f}% | "
                          f"Z={val.altman_z_score or 0:.2f} ({val.z_score_zone})")
                else:
                    results["skipped"].append(company.ticker)

            except Exception as e:
                print(f"  [{company.ticker}] ✗ Error: {e}")
                results["errors"].append({"ticker": company.ticker, "error": str(e)})

        session.commit()
        print(f"\n{'='*60}")
        print(f"  DONE: {len(results['computed'])} computed, "
              f"{len(results['errors'])} errors, {len(results['skipped'])} skipped")
        print(f"{'='*60}\n")

    except Exception as e:
        session.rollback()
        print(f"  FATAL ERROR: {e}")
        raise
    finally:
        session.close()

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO SWEEP: Run Bear/Base/Bull across entire database
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario_sweep(scenario_names: Optional[list[str]] = None) -> dict:
    """
    Run valuations across multiple scenarios for all active companies.
    Modifying a single config file changes the entire sweep.

    Returns:
        Dict keyed by scenario name, each containing list of valuations.
    """
    cfg = _cfg()
    scenarios = scenario_names or cfg.scenario_names()
    results = {}

    for scenario_name in scenarios:
        sc = cfg.scenario(scenario_name)
        print(f"\n{'='*60}")
        print(f"  SCENARIO SWEEP: {sc.label} ({scenario_name})")
        print(f"  ERP={sc.equity_risk_premium:.4f} | TG={sc.terminal_growth_rate:.4f} | "
              f"FCF_mult={sc.fcf_growth_multiplier} | WACC_adj={sc.wacc_adjustment}")
        print(f"{'='*60}")

        session = get_session()
        scenario_results = []

        try:
            companies = session.query(Company).filter(Company.is_active == True).all()

            for company in companies:
                try:
                    val = run_valuation_for_company(session, company, scenario_name=scenario_name)
                    if val:
                        scenario_results.append({
                            "ticker": company.ticker,
                            "scenario": scenario_name,
                            "signal": val.signal,
                            "intrinsic": val.intrinsic_value_dcf,
                            "market": val.market_price,
                            "mos": val.margin_of_safety_pct,
                            "z_score": val.altman_z_score,
                        })
                except Exception as e:
                    print(f"  [{company.ticker}] ✗ {scenario_name}: {e}")

            session.commit()
        except Exception as e:
            session.rollback()
            print(f"  SCENARIO ERROR: {e}")
        finally:
            session.close()

        results[scenario_name] = scenario_results

    return results


# ── Module Self-Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Running Valuation Engine (base scenario)...")
    result = run_all_valuations()
    print(f"\nBase Results: {len(result['computed'])} computed, {len(result['errors'])} errors")
