"""
math_config.py — Modular Configuration Engine for Tőzsde Figyelő
Loads config.yaml and exposes every financial assumption as a dynamic,
overrideable parameter. Supports sector-specific overrides and
Bear/Base/Bull scenario loops.

Usage:
    cfg = MathConfig()                              # Load defaults
    cfg.get("terminal_growth_rate")                 # → 0.025
    cfg.get("terminal_growth_rate", sector="Technology")  # → 0.04
    bear = cfg.scenario("bear")                     # Returns ScenarioConfig
    bear.get("mos_strong_buy")                      # → 40.0
    cfg.get_z_coefficients(sector="Financial Services")  # Custom coefficients
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional
from copy import deepcopy

import yaml


# ── Default config path ──────────────────────────────────────────────────────

CONFIG_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO CONFIG (immutable snapshot of config for a specific scenario)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=False)
class ScenarioConfig:
    """
    A resolved configuration snapshot for a specific scenario.
    All parameters are pre-computed (global + scenario adjustments applied).
    """
    label: str
    # ── DCF ──
    risk_free_rate: float = 0.0425
    market_return: float = 0.10
    equity_risk_premium: float = 0.0575
    corporate_tax_rate: float = 0.21
    dcf_projection_years: int = 10
    terminal_growth_rate: float = 0.025
    max_fcf_growth_cap: float = 0.25
    min_fcf_growth_floor: float = -0.10
    min_wacc: float = 0.06
    max_wacc: float = 0.25
    # ── Signals ──
    mos_strong_buy: float = 30.0
    mos_buy: float = 20.0
    mos_hold_floor: float = 0.0
    # ── Z-Score ──
    z_safe_threshold: float = 2.99
    z_grey_threshold: float = 1.81
    # ── Scenario multipliers (for reference) ──
    fcf_growth_multiplier: float = 1.0
    wacc_adjustment: float = 0.0

    def get(self, key: str, default: Any = None) -> Any:
        """Get a parameter by name."""
        return getattr(self, key, default)


# ══════════════════════════════════════════════════════════════════════════════
# MATH CONFIG (main configuration class)
# ══════════════════════════════════════════════════════════════════════════════

class MathConfig:
    """
    Central configuration engine that loads config.yaml and provides:
    - Global parameter access
    - Sector-specific overrides
    - Scenario resolution (Bear/Base/Bull)
    - Z-score coefficient access with sector overrides
    - API rate limit parameters
    """

    def __init__(self, config_path: str = CONFIG_PATH):
        self._path = config_path
        self._raw: dict = {}
        self._global: dict = {}
        self._sectors: dict = {}
        self._scenarios: dict = {}
        self._z_coefficients: dict = {}
        self._api_limits: dict = {}
        self.load()

    def load(self):
        """Load configuration from YAML file."""
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                self._raw = yaml.safe_load(f) or {}
        else:
            print(f"[MathConfig] Warning: {self._path} not found, using hardcoded defaults.")
            self._raw = {}

        self._global        = self._raw.get("global", {})
        self._sectors       = self._raw.get("sector_overrides", {})
        self._scenarios     = self._raw.get("scenarios", {})
        self._z_coefficients = self._raw.get("altman_z_coefficients", {})
        self._api_limits    = self._raw.get("api_limits", {})

    def reload(self):
        """Hot-reload config from disk."""
        self.load()

    # ── Parameter Access ──────────────────────────────────────────────────────

    def get(self, key: str, sector: Optional[str] = None, default: Any = None) -> Any:
        """
        Get a configuration parameter.
        If sector is provided, sector-specific override takes priority over global.

        Examples:
            cfg.get("terminal_growth_rate")                  → 0.025
            cfg.get("terminal_growth_rate", sector="Technology")  → 0.04
        """
        # Check sector override first
        if sector and sector in self._sectors:
            sector_val = self._sectors[sector].get(key)
            if sector_val is not None:
                return sector_val

        # Fall back to global
        global_val = self._global.get(key)
        if global_val is not None:
            return global_val

        return default

    def get_all_global(self) -> dict:
        """Return all global parameters as a dict."""
        return deepcopy(self._global)

    def get_sector_names(self) -> list[str]:
        """Return all sector names that have overrides."""
        return list(self._sectors.keys())

    def get_sector_config(self, sector: str) -> dict:
        """Return the full override dict for a sector (merged with globals)."""
        result = deepcopy(self._global)
        if sector in self._sectors:
            result.update(self._sectors[sector])
        return result

    # ── Z-Score Coefficients ──────────────────────────────────────────────────

    def get_z_coefficients(self, sector: Optional[str] = None) -> dict:
        """
        Get Altman Z-score coefficients, with optional sector overrides.

        Returns:
            dict with keys: x1_wc_ta, x2_re_ta, x3_ebit_ta, x4_mcap_tl, x5_rev_ta
        """
        base = deepcopy(self._z_coefficients) or {
            "x1_wc_ta": 1.2, "x2_re_ta": 1.4, "x3_ebit_ta": 3.3,
            "x4_mcap_tl": 0.6, "x5_rev_ta": 1.0,
        }

        # Check for sector-specific Z coefficients
        if sector and sector in self._sectors:
            sector_z = self._sectors[sector].get("altman_z_coefficients")
            if sector_z:
                base.update(sector_z)

        return base

    # ── Scenario Resolution ───────────────────────────────────────────────────

    def scenario(self, name: str = "base", sector: Optional[str] = None) -> ScenarioConfig:
        """
        Resolve a complete ScenarioConfig by applying scenario adjustments
        on top of global (and optionally sector) defaults.

        Args:
            name: Scenario name ("base", "bear", "bull", "deep_value")
            sector: Optional sector for sector-specific base values

        Returns:
            ScenarioConfig with all parameters resolved.

        Examples:
            cfg.scenario("bear")                          # Bear case, global
            cfg.scenario("bull", sector="Technology")     # Bull case, tech sector
        """
        scenario_data = self._scenarios.get(name, {})

        # Start from global/sector base
        rf  = self.get("risk_free_rate", sector=sector, default=0.0425)
        rm  = self.get("market_return", sector=sector, default=0.10)
        erp = self.get("equity_risk_premium", sector=sector, default=0.0575)
        tg  = self.get("terminal_growth_rate", sector=sector, default=0.025)
        cap = self.get("max_fcf_growth_cap", sector=sector, default=0.25)
        flr = self.get("min_fcf_growth_floor", sector=sector, default=-0.10)

        # Apply scenario adjustments
        erp_adj  = scenario_data.get("erp_adjustment", 0.0)
        wacc_adj = scenario_data.get("wacc_adjustment", 0.0)
        tg_adj   = scenario_data.get("terminal_growth_adjustment", 0.0)
        fcf_mult = scenario_data.get("fcf_growth_multiplier", 1.0)

        return ScenarioConfig(
            label=scenario_data.get("label", name.title()),
            risk_free_rate=rf,
            market_return=rm,
            equity_risk_premium=erp + erp_adj,
            corporate_tax_rate=self.get("corporate_tax_rate", default=0.21),
            dcf_projection_years=self.get("dcf_projection_years", default=10),
            terminal_growth_rate=max(0.005, tg + tg_adj),  # Floor at 0.5%
            max_fcf_growth_cap=cap,
            min_fcf_growth_floor=flr,
            min_wacc=self.get("min_wacc", default=0.06) + max(0, wacc_adj),
            max_wacc=self.get("max_wacc", default=0.25),
            mos_strong_buy=scenario_data.get("mos_strong_buy", self.get("mos_strong_buy", default=30.0)),
            mos_buy=scenario_data.get("mos_buy", self.get("mos_buy", default=20.0)),
            mos_hold_floor=self.get("mos_hold_floor", default=0.0),
            z_safe_threshold=scenario_data.get("z_safe_threshold", self.get("z_safe_threshold", default=2.99)),
            z_grey_threshold=scenario_data.get("z_grey_threshold", self.get("z_grey_threshold", default=1.81)),
            fcf_growth_multiplier=fcf_mult,
            wacc_adjustment=wacc_adj,
        )

    def scenario_names(self) -> list[str]:
        """Return all available scenario names."""
        return list(self._scenarios.keys())

    # ── API Rate Limits ───────────────────────────────────────────────────────

    def api_limit(self, provider: str, key: str, default: Any = None) -> Any:
        """
        Get an API rate limit parameter.
        Example: cfg.api_limit("fmp", "max_concurrent")  → 5
        """
        return self._api_limits.get(provider, {}).get(key, default)

    def api_limits_for(self, provider: str) -> dict:
        """Get all rate limit params for a provider."""
        return deepcopy(self._api_limits.get(provider, {}))

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the full config to a dict (for API exposure)."""
        return {
            "global": deepcopy(self._global),
            "sectors": self.get_sector_names(),
            "scenarios": self.scenario_names(),
            "z_coefficients": deepcopy(self._z_coefficients),
            "api_limits": deepcopy(self._api_limits),
        }

    def __repr__(self) -> str:
        return (
            f"<MathConfig scenarios={self.scenario_names()} "
            f"sectors={self.get_sector_names()} "
            f"rf={self.get('risk_free_rate')} erp={self.get('equity_risk_premium')}>"
        )


# ── Global singleton ──────────────────────────────────────────────────────────
_config_instance: Optional[MathConfig] = None


def get_config() -> MathConfig:
    """Get or create the global MathConfig singleton."""
    global _config_instance
    if _config_instance is None:
        _config_instance = MathConfig()
    return _config_instance


# ── Module Self-Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = MathConfig()
    print(cfg)
    print(f"\nGlobal terminal growth:    {cfg.get('terminal_growth_rate')}")
    print(f"Tech terminal growth:      {cfg.get('terminal_growth_rate', sector='Technology')}")
    print(f"Utilities terminal growth: {cfg.get('terminal_growth_rate', sector='Utilities')}")
    print(f"\nZ-Coefficients (global):   {cfg.get_z_coefficients()}")
    print(f"Z-Coefficients (finance):  {cfg.get_z_coefficients(sector='Financial Services')}")

    for name in cfg.scenario_names():
        sc = cfg.scenario(name)
        print(f"\nScenario '{name}': {sc.label}")
        print(f"  ERP={sc.equity_risk_premium:.4f}  TG={sc.terminal_growth_rate:.4f}  "
              f"MoS_BUY={sc.mos_buy}  WACC_adj={sc.wacc_adjustment}")

    print(f"\nFMP rate limit: {cfg.api_limits_for('fmp')}")
