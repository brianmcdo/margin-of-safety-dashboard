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
    cache_ttl_hours: float = 24
    max_workers: int = 6
    # Empty means the built-in sample list in src/data/universe.py.
    sample: list[str] = Field(default_factory=list)


class FredSettings(BaseModel):
    series: str = "DAAA"
    fallback_aaa_yield: float = 6.0


class LoggingSettings(BaseModel):
    """Console stays at ``level``. The file keeps ``file_level``, usually DEBUG."""

    level: str = "INFO"
    file: str = "logs/value_signals.log"
    file_level: str = "DEBUG"


class DataSettings(BaseModel):
    source: str = "yfinance"
    # Optional. Prefer the environment variable so the key is not committed.
    fmp_api_key: str = ""
    fmp_api_key_env: str = "FMP_API_KEY"
    sqlite_path: str = "data/value_signals.db"
    request_timeout_seconds: float = 25
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    rate_limit_backoff_seconds: float = 8.0


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
    # Low case: growth minus this spread, discount plus this spread.
    # High case: growth plus this spread, discount minus this spread.
    growth_spread: float = 0.02
    discount_spread: float = 0.02


class NcavSettings(BaseModel):
    discount_receivables: float = 0.75
    discount_inventory: float = 0.50


class MosSettings(BaseModel):
    strong: float = 0.40
    moderate: float = 0.30
    minimum: float = 0.30


class ValuationSettings(BaseModel):
    graham_growth_percent: float = 5
    max_graham_growth_percent: float = 15
    max_graham_implied_pe: float = 40
    dcf: DcfSettings = Field(default_factory=DcfSettings)
    ncav: NcavSettings = Field(default_factory=NcavSettings)
    mos: MosSettings = Field(default_factory=MosSettings)


class ScoringWeights(BaseModel):
    """Composite weights. They are renormalized, so a zero weight drops out."""

    margin_of_safety: float = 0.50
    cheapness: float = 0.25
    quality: float = 0.25
    balance_sheet: float = 0.0


class QualitySettings(BaseModel):
    tax_rate: float = 0.21
    low_return_threshold: float = 0.08
    min_return_years: int = 2
    minimum: float = 40
    altman_safe: float = 2.99
    altman_distress: float = 1.81
    f_weight: float = 0.40
    distress_weight: float = 0.30
    returns_weight: float = 0.30


class ScoringSettings(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    quality: QualitySettings = Field(default_factory=QualitySettings)


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


class CommodityQuote(BaseModel):
    """A planning price and the price trailing cash flow is assumed to reflect."""

    normalized: float
    reference: float
    unit: str


class CommoditySettings(BaseModel):
    enabled: bool = True
    sector_keywords: list[str] = Field(
        default_factory=lambda: ["energy", "basic materials", "materials", "mining"]
    )
    industry_keywords: list[str] = Field(
        default_factory=lambda: [
            "oil & gas",
            "oil",
            "gas e&p",
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
    prices: dict[str, CommodityQuote] = Field(
        default_factory=lambda: {
            "oil": CommodityQuote(normalized=70, reference=80, unit="USD per barrel"),
            "gold": CommodityQuote(normalized=2000, reference=2300, unit="USD per ounce"),
            "copper": CommodityQuote(normalized=4.0, reference=4.2, unit="USD per pound"),
            "metals": CommodityQuote(normalized=1.0, reference=1.0, unit="index versus reference cash flow"),
        }
    )
    sensitivity_price_multipliers: list[float] = Field(
        default_factory=lambda: [0.70, 0.85, 1.00, 1.15, 1.30]
    )
    sensitivity_discount_rates: list[float] = Field(
        default_factory=lambda: [0.08, 0.10, 0.12]
    )
    low_cost_min_quality: float = 60
    low_cost_max_debt_equity: float = 0.50


class BacktestSettings(BaseModel):
    """Equal-weight price returns. Horizons stay at six months and one year."""

    benchmark: str = "SPY"
    price_tolerance_days: int = 10


class NewsSettings(BaseModel):
    """Keyword check on recent Yahoo headlines. Not a language model."""

    enabled: bool = True
    lookback_days: int = 14
    max_headlines: int = 8
    min_negative_headlines: int = 2
    downrank: bool = False
    penalty: float = 8.0


class PortfolioSettings(BaseModel):
    """Caps for the suggested basket. Weights are fractions of the book."""

    max_sector_weight: float = 0.25
    max_position_size: float = 0.10
    min_holdings: int = 8


class ScheduleSettings(BaseModel):
    day_of_week: str = "mon-fri"
    hour: int = 7
    minute: int = 0


class Settings(BaseModel):
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    fred: FredSettings = Field(default_factory=FredSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    screens: ScreenSettings = Field(default_factory=ScreenSettings)
    valuation: ValuationSettings = Field(default_factory=ValuationSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    commodity: CommoditySettings = Field(default_factory=CommoditySettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)
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
