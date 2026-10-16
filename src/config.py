"""Load the YAML configuration into typed settings."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


def project_root() -> Path:
    """Directory that contains config.yaml and the src package."""
    return Path(__file__).resolve().parents[1]


class UniverseSettings(BaseModel):
    default: str = "sample"
    cache_ttl_hours: float = 20
    max_workers: int = 6


class FredSettings(BaseModel):
    series: str = "DAAA"
    fallback_aaa_yield: float = 6.0


class DataSettings(BaseModel):
    source: str = "yfinance"
    fmp_api_key_env: str = "FMP_API_KEY"
    sqlite_path: str = "data/value_signals.db"
    request_timeout_seconds: float = 25


class ScreenToggles(BaseModel):
    max_pe: bool = True
    max_pb: bool = True
    max_ev_ebit: bool = True
    max_debt_equity: bool = True
    min_current_ratio: bool = True
    positive_eps: bool = True
    positive_fcf: bool = True
    max_price_to_ncav: bool = False
    min_market_cap: bool = True
    min_avg_volume: bool = True


class ScreenThresholds(BaseModel):
    max_pe: float = 15
    max_pb: float = 1.5
    max_ev_ebit: float = 10
    max_debt_equity: float = 1.0
    min_current_ratio: float = 1.5
    max_price_to_ncav: float = 1.0
    min_market_cap: float = 500_000_000
    min_avg_volume: float = 200_000
    pe_mode: str = "absolute"
    max_pe_sector_percentile: float = 0.40


class ScreenSettings(BaseModel):
    enabled: ScreenToggles = Field(default_factory=ScreenToggles)
    thresholds: ScreenThresholds = Field(default_factory=ScreenThresholds)
    deep_value_bypass: bool = True


class DcfSettings(BaseModel):
    years: int = 8
    growth_rate: float = 0.03
    discount_rate: float = 0.10
    terminal_growth: float = 0.025
    max_terminal_share: float = 0.65
    normalize_years: int = 3


class NcavSettings(BaseModel):
    discount_receivables: float = 0.75
    discount_inventory: float = 0.50


class MosSettings(BaseModel):
    strong: float = 0.40
    moderate: float = 0.25
    minimum: float = 0.25


class ValuationSettings(BaseModel):
    graham_growth_percent: float = 5
    max_graham_growth_percent: float = 15
    max_graham_implied_pe: float = 40
    dcf: DcfSettings = Field(default_factory=DcfSettings)
    ncav: NcavSettings = Field(default_factory=NcavSettings)
    mos: MosSettings = Field(default_factory=MosSettings)


class ScoringWeights(BaseModel):
    margin_of_safety: float = 0.50
    cheapness: float = 0.25
    balance_sheet: float = 0.15
    quality: float = 0.10


class ScoringSettings(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)


class EmailSettings(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    from_env: str = "ALERT_EMAIL_FROM"
    password_env: str = "ALERT_EMAIL_PASSWORD"
    username_env: str = "ALERT_EMAIL_USER"
    to: list[str] = Field(default_factory=list)
    use_tls: bool = True


class AlertSettings(BaseModel):
    console: bool = True
    email: EmailSettings = Field(default_factory=EmailSettings)


class CommoditySettings(BaseModel):
    enabled: bool = True
    industry_keywords: list[str] = Field(
        default_factory=lambda: [
            "oil & gas",
            "oil",
            "gold",
            "silver",
            "copper",
            "mining",
            "coal",
            "uranium",
            "steel",
            "aluminum",
            "aluminium",
            "iron",
            "lithium",
            "metals",
        ]
    )
    sensitivity_price_multipliers: list[float] = Field(
        default_factory=lambda: [0.70, 0.85, 1.00, 1.15, 1.30]
    )
    sensitivity_discount_rates: list[float] = Field(
        default_factory=lambda: [0.08, 0.10, 0.12]
    )


class ScheduleSettings(BaseModel):
    day_of_week: str = "mon-fri"
    hour: int = 7
    minute: int = 0


class Settings(BaseModel):
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    fred: FredSettings = Field(default_factory=FredSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    screens: ScreenSettings = Field(default_factory=ScreenSettings)
    valuation: ValuationSettings = Field(default_factory=ValuationSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    commodity: CommoditySettings = Field(default_factory=CommoditySettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)


def load_settings(path: str | Path | None = None) -> Settings:
    """Read config.yaml. Missing keys keep the conservative defaults."""
    config_path = Path(path) if path else project_root() / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a mapping: {config_path}")
    return Settings.model_validate(raw)


def resolve_path(relative: str | Path) -> Path:
    """Resolve a project-relative path. Absolute paths are left unchanged."""
    path = Path(relative)
    if path.is_absolute():
        return path
    return project_root() / path
