"""
intraday_monitor.py — Phase C: Intraday Price Monitor for Tőzsde Figyelő
Lightweight loop running during market hours.

Polls 15-minute delayed prices via yfinance and compares against
the local Valuations table for real-time margin-of-safety alerts.

Usage:
    python intraday_monitor.py               # Run during market hours
    python intraday_monitor.py --once        # Single check, then exit
    python intraday_monitor.py --interval 60 # Custom interval (seconds)
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import yfinance as yf
from sqlalchemy.orm import Session

from models import (
    get_session, init_quant_db,
    Company, Valuation, DailyPrice,
    get_latest_valuation,
)

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_INTERVAL_SECONDS = 900  # 15 minutes
MARKET_OPEN_HOUR   = 9    # 9:30 ET (we use 9 for margin)
MARKET_CLOSE_HOUR  = 16   # 16:00 ET
MOS_ALERT_THRESHOLD = 20.0  # Alert when MoS exceeds this


# ══════════════════════════════════════════════════════════════════════════════
# INTRADAY PRICE CHECK
# ══════════════════════════════════════════════════════════════════════════════

def check_intraday_prices() -> list[dict]:
    """
    Pull current prices for all tracked companies and compare
    against the latest Valuation record.

    Returns a list of alert dicts for stocks with significant margin of safety.
    """
    session = get_session()
    alerts = []

    try:
        companies = session.query(Company).filter(Company.is_active == True).all()

        if not companies:
            print("  [Monitor] No active companies to monitor.")
            return alerts

        tickers = [c.ticker for c in companies]
        print(f"\n  [Monitor] Checking {len(tickers)} tickers: {', '.join(tickers)}")

        # Batch download current prices
        try:
            data = yf.download(tickers, period="1d", progress=False, threads=True)
        except Exception as e:
            print(f"  [Monitor] ✗ yfinance download error: {e}")
            return alerts

        for company in companies:
            ticker = company.ticker
            try:
                # Get current price
                if len(tickers) == 1:
                    current_price = float(data["Close"].iloc[-1]) if not data.empty else None
                else:
                    if ticker in data["Close"].columns:
                        price_series = data["Close"][ticker].dropna()
                        current_price = float(price_series.iloc[-1]) if not price_series.empty else None
                    else:
                        current_price = None

                if current_price is None or current_price <= 0:
                    continue

                # Get latest valuation
                val = get_latest_valuation(session, company.id)
                if not val or val.intrinsic_value_dcf is None:
                    continue

                intrinsic = val.intrinsic_value_dcf
                mos = (intrinsic - current_price) / intrinsic * 100 if intrinsic > 0 else 0

                # Check for alert conditions
                alert = {
                    "ticker":         ticker,
                    "name":           company.name,
                    "current_price":  round(current_price, 2),
                    "intrinsic_value": round(intrinsic, 2),
                    "margin_of_safety": round(mos, 2),
                    "valuation_signal": val.signal,
                    "z_score":        val.altman_z_score,
                    "z_zone":         val.z_score_zone,
                    "timestamp":      datetime.now(timezone.utc).isoformat(),
                }

                if mos >= MOS_ALERT_THRESHOLD and val.z_score_zone != "DISTRESS":
                    alert["alert_type"] = "OPPORTUNITY"
                    alert["message"] = (
                        f"🟢 {ticker} @ ${current_price:.2f} — "
                        f"MoS={mos:.1f}% (intrinsic=${intrinsic:.2f}) | "
                        f"Z={val.altman_z_score:.2f} ({val.z_zone})"
                    )
                    print(f"  *** ALERT: {alert['message']}")
                elif mos >= MOS_ALERT_THRESHOLD and val.z_score_zone == "DISTRESS":
                    alert["alert_type"] = "VALUE_TRAP_WARNING"
                    alert["message"] = (
                        f"⚠️ {ticker} @ ${current_price:.2f} — "
                        f"MoS={mos:.1f}% but Z={val.altman_z_score:.2f} (DISTRESS) — VALUE TRAP"
                    )
                    print(f"  *** WARNING: {alert['message']}")
                else:
                    alert["alert_type"] = "NORMAL"
                    print(f"  [{ticker}] ${current_price:.2f} | MoS={mos:.1f}% | Z={val.altman_z_score:.2f}")

                alerts.append(alert)

            except Exception as e:
                print(f"  [{ticker}] ✗ Error: {e}")

    except Exception as e:
        print(f"  [Monitor] ✗ Error: {e}")
    finally:
        session.close()

    return alerts


# ══════════════════════════════════════════════════════════════════════════════
# MONITOR LOOP
# ══════════════════════════════════════════════════════════════════════════════

def is_market_hours() -> bool:
    """Check if US market is currently open (approximate)."""
    # Convert current UTC time to US Eastern (UTC-4 or UTC-5)
    now_utc = datetime.now(timezone.utc)
    # Approximate Eastern time (UTC-4 during DST, UTC-5 otherwise)
    eastern = now_utc - timedelta(hours=4)

    hour = eastern.hour
    weekday = eastern.weekday()  # 0=Monday, 6=Sunday

    # Markets closed on weekends
    if weekday >= 5:
        return False

    # Market hours: 9:30 AM - 4:00 PM ET
    if hour < MARKET_OPEN_HOUR or hour >= MARKET_CLOSE_HOUR:
        return False

    return True


def run_monitor(interval: int = DEFAULT_INTERVAL_SECONDS, once: bool = False):
    """
    Main monitor loop.
    Runs during market hours, checking prices at the specified interval.
    """
    init_quant_db()

    print("\n" + "=" * 60)
    print("  INTRADAY MONITOR — Phase C")
    print(f"  Interval: {interval}s | Mode: {'single check' if once else 'continuous'}")
    print("=" * 60)

    while True:
        if not once and not is_market_hours():
            print(f"\n  [Monitor] Market closed. Sleeping 5 minutes...")
            time.sleep(300)
            continue

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n  ── Intraday Check @ {now} ──")

        alerts = check_intraday_prices()

        opportunities = [a for a in alerts if a.get("alert_type") == "OPPORTUNITY"]
        traps = [a for a in alerts if a.get("alert_type") == "VALUE_TRAP_WARNING"]

        if opportunities:
            print(f"\n  🟢 {len(opportunities)} OPPORTUNITIES detected!")
        if traps:
            print(f"  ⚠️  {len(traps)} VALUE TRAP warnings!")

        if once:
            print("\n  [Monitor] Single check complete. Exiting.")
            return alerts

        print(f"\n  [Monitor] Next check in {interval}s...")
        time.sleep(interval)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Intraday Price Monitor (Phase C)")
    parser.add_argument("--once", action="store_true", help="Single check, then exit")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"Check interval in seconds (default: {DEFAULT_INTERVAL_SECONDS})")
    args = parser.parse_args()

    run_monitor(interval=args.interval, once=args.once)
