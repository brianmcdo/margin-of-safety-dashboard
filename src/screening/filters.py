"""Quantitative value screens. Every threshold is optional."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config import Settings
from src.data.models import StockFundamentals, as_float
from src.valuation.ncav import ncav_per_share, strict_ncav_per_share

FINANCIAL_SECTOR_MARKERS = ("financial",)
LIQUIDITY_CODES = {"min_market_cap", "min_avg_volume"}
NET_NET_BYPASS_CODES = {
    "max_pe",
    "max_pb",
    "max_ev_ebit",
    "max_debt_equity",
    "min_current_ratio",
    "positive_eps",
    "positive_fcf",
    "max_price_to_ncav",
}


@dataclass
class ComputedMetrics:
    pe: float | None = None
    pb: float | None = None
    enterprise_value: float | None = None
    ev_ebit: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    ncav_per_share: float | None = None
    ncav_strict_per_share: float | None = None
    price_to_ncav: float | None = None
    market_cap: float | None = None
    average_volume: float | None = None
    pe_sector_percentile: float | None = None
    pb_percentile: float | None = None
    pe_percentile: float | None = None
    ev_ebit_percentile: float | None = None


@dataclass
class ScreenResult:
    ticker: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    failure_codes: list[str] = field(default_factory=list)
    bypassed: list[str] = field(default_factory=list)
    metrics: ComputedMetrics = field(default_factory=ComputedMetrics)
    is_financial: bool = False
    net_net: bool = False
    liquidity_ok: bool = False


def is_financial(stock: StockFundamentals) -> bool:
    """Banks and insurers are not valued like industrial net-nets."""
    sector = (stock.sector or "").lower()
    industry = (stock.industry or "").lower()
    return any(marker in sector or marker in industry for marker in FINANCIAL_SECTOR_MARKERS)


def compute_metrics(stock: StockFundamentals, settings: Settings) -> ComputedMetrics:
    """Derive the ratios the screen and the score both use."""
    price = as_float(stock.price)
    eps = as_float(stock.trailing_eps)
    book = as_float(stock.book_value_per_share)
    ebit = as_float(stock.ebit)
    debt = as_float(stock.total_debt)
    cash = as_float(stock.cash)
    market_cap = as_float(stock.market_cap)
    if market_cap is None and price is not None and stock.shares_outstanding:
        shares = as_float(stock.shares_outstanding)
        if shares is not None and shares > 0:
            market_cap = price * shares

    pe = price / eps if price is not None and eps is not None and eps > 0 else None
    pb = price / book if price is not None and book is not None and book > 0 else None

    enterprise = as_float(stock.enterprise_value)
    if market_cap is not None and debt is not None and cash is not None:
        enterprise = market_cap + debt - cash
    ev_ebit = (
        enterprise / ebit if enterprise is not None and ebit is not None and ebit > 0 else None
    )

    ncav = ncav_per_share(stock.current_assets, stock.total_liabilities, stock.shares_outstanding)
    strict, _note = strict_ncav_per_share(
        stock.current_assets,
        stock.cash,
        stock.receivables,
        stock.inventory,
        stock.total_liabilities,
        stock.shares_outstanding,
        receivables_factor=settings.valuation.ncav.discount_receivables,
        inventory_factor=settings.valuation.ncav.discount_inventory,
    )
    price_to_ncav = price / ncav if price is not None and ncav is not None and ncav > 0 else None
    current_ratio = as_float(stock.current_ratio)
    if (
        current_ratio is None
        and stock.current_assets is not None
        and stock.current_liabilities is not None
        and stock.current_liabilities > 0
    ):
        current_ratio = stock.current_assets / stock.current_liabilities

    return ComputedMetrics(
        pe=pe,
        pb=pb,
        enterprise_value=enterprise,
        ev_ebit=ev_ebit,
        debt_to_equity=as_float(stock.debt_to_equity),
        current_ratio=current_ratio,
        ncav_per_share=ncav,
        ncav_strict_per_share=strict,
        price_to_ncav=price_to_ncav,
        market_cap=market_cap,
        average_volume=as_float(stock.average_volume),
    )


def _percentile(value: float, sample: list[float]) -> float | None:
    """0 means cheapest (lowest multiple). None when the sample is too small."""
    others = [item for item in sample if item is not None]
    if value is None or len(others) < 5:
        return None
    below = sum(1 for item in others if item < value)
    return below / (len(others) - 1)


def attach_peer_percentiles(
    metrics_by_ticker: dict[str, ComputedMetrics],
    stocks: list[StockFundamentals],
) -> None:
    """Fill universe and sector percentiles in place."""
    pe_sample = [item.pe for item in metrics_by_ticker.values() if item.pe is not None]
    pb_sample = [item.pb for item in metrics_by_ticker.values() if item.pb is not None]
    ev_sample = [item.ev_ebit for item in metrics_by_ticker.values() if item.ev_ebit is not None]
    sector_pe: dict[str, list[float]] = {}
    for stock in stocks:
        metrics = metrics_by_ticker.get(stock.ticker)
        if metrics is None or metrics.pe is None:
            continue
        sector_pe.setdefault(stock.sector or "Unknown", []).append(metrics.pe)

    for stock in stocks:
        metrics = metrics_by_ticker.get(stock.ticker)
        if metrics is None:
            continue
        if metrics.pe is not None:
            metrics.pe_percentile = _percentile(metrics.pe, [v for v in pe_sample if v is not None])
            metrics.pe_sector_percentile = _percentile(
                metrics.pe,
                sector_pe.get(stock.sector or "Unknown", []),
            )
        if metrics.pb is not None:
            metrics.pb_percentile = _percentile(metrics.pb, [v for v in pb_sample if v is not None])
        if metrics.ev_ebit is not None:
            metrics.ev_ebit_percentile = _percentile(
                metrics.ev_ebit, [v for v in ev_sample if v is not None]
            )


def _fail(failures: list[tuple[str, str]], code: str, message: str) -> None:
    failures.append((code, message))


def screen_stock(
    stock: StockFundamentals,
    metrics: ComputedMetrics,
    settings: Settings,
) -> ScreenResult:
    """Apply the enabled filters. Missing data fails a filter that needs it."""
    enabled = settings.screens.enabled
    limits = settings.screens.thresholds
    financial = is_financial(stock)
    failures: list[tuple[str, str]] = []

    if enabled.positive_eps:
        eps = as_float(stock.trailing_eps)
        if eps is None or eps <= 0:
            _fail(failures, "positive_eps", "trailing EPS is not positive")
    if enabled.positive_fcf:
        fcf = as_float(stock.free_cash_flow)
        if fcf is None or fcf <= 0:
            _fail(failures, "positive_fcf", "free cash flow is not positive")
    if enabled.max_pe:
        if limits.pe_mode == "sector_percentile" and metrics.pe_sector_percentile is not None:
            if metrics.pe_sector_percentile > limits.max_pe_sector_percentile:
                _fail(
                    failures,
                    "max_pe",
                    (
                        f"P/E {metrics.pe:.1f} is in the "
                        f"{metrics.pe_sector_percentile:.0%} percentile of its sector, "
                        f"above {limits.max_pe_sector_percentile:.0%}"
                    ),
                )
        elif metrics.pe is None:
            _fail(failures, "max_pe", "P/E is missing")
        elif metrics.pe > limits.max_pe:
            _fail(failures, "max_pe", f"P/E {metrics.pe:.1f} is above {limits.max_pe:g}")
    if enabled.max_pb:
        if metrics.pb is None:
            _fail(failures, "max_pb", "P/B is missing")
        elif metrics.pb > limits.max_pb:
            _fail(failures, "max_pb", f"P/B {metrics.pb:.2f} is above {limits.max_pb:g}")
    if enabled.max_ev_ebit and not financial:
        if metrics.ev_ebit is None:
            _fail(failures, "max_ev_ebit", "EV/EBIT is missing")
        elif metrics.ev_ebit > limits.max_ev_ebit:
            _fail(
                failures,
                "max_ev_ebit",
                f"EV/EBIT {metrics.ev_ebit:.1f} is above {limits.max_ev_ebit:g}",
            )
    if enabled.max_debt_equity:
        if metrics.debt_to_equity is None:
            _fail(failures, "max_debt_equity", "debt/equity is missing")
        elif metrics.debt_to_equity > limits.max_debt_equity:
            _fail(
                failures,
                "max_debt_equity",
                f"debt/equity {metrics.debt_to_equity:.2f} is above {limits.max_debt_equity:g}",
            )
    if enabled.min_current_ratio and not financial:
        if metrics.current_ratio is None:
            _fail(failures, "min_current_ratio", "current ratio is missing")
        elif metrics.current_ratio < limits.min_current_ratio:
            _fail(
                failures,
                "min_current_ratio",
                f"current ratio {metrics.current_ratio:.2f} is below {limits.min_current_ratio:g}",
            )
    if enabled.max_price_to_ncav and not financial:
        if metrics.price_to_ncav is None:
            _fail(failures, "max_price_to_ncav", "price/NCAV is missing or NCAV is not positive")
        elif metrics.price_to_ncav > limits.max_price_to_ncav:
            _fail(
                failures,
                "max_price_to_ncav",
                f"price/NCAV {metrics.price_to_ncav:.2f} is above {limits.max_price_to_ncav:g}",
            )
    if enabled.min_market_cap:
        if metrics.market_cap is None:
            _fail(failures, "min_market_cap", "market cap is missing")
        elif metrics.market_cap < limits.min_market_cap:
            _fail(
                failures,
                "min_market_cap",
                f"market cap {metrics.market_cap:,.0f} is below {limits.min_market_cap:,.0f}",
            )
    if enabled.min_avg_volume:
        if metrics.average_volume is None:
            _fail(failures, "min_avg_volume", "average volume is missing")
        elif metrics.average_volume < limits.min_avg_volume:
            _fail(
                failures,
                "min_avg_volume",
                f"average volume {metrics.average_volume:,.0f} is below {limits.min_avg_volume:,.0f}",
            )

    net_net = (
        not financial
        and metrics.ncav_per_share is not None
        and metrics.ncav_per_share > 0
        and stock.price is not None
        and stock.price < metrics.ncav_per_share
    )
    bypassed: list[str] = []
    if settings.screens.deep_value_bypass and net_net:
        kept: list[tuple[str, str]] = []
        for code, message in failures:
            if code in NET_NET_BYPASS_CODES:
                bypassed.append(message)
            else:
                kept.append((code, message))
        failures = kept

    liquidity_failures = [code for code, _message in failures if code in LIQUIDITY_CODES]
    liquidity_ok = not liquidity_failures and metrics.market_cap is not None and stock.price is not None
    return ScreenResult(
        ticker=stock.ticker,
        passed=not failures,
        failures=[message for _code, message in failures],
        failure_codes=[code for code, _message in failures],
        bypassed=bypassed,
        metrics=metrics,
        is_financial=financial,
        net_net=net_net,
        liquidity_ok=liquidity_ok,
    )


def metrics_dict(metrics: ComputedMetrics) -> dict[str, float | None]:
    return {
        "pe": metrics.pe,
        "pb": metrics.pb,
        "ev_ebit": metrics.ev_ebit,
        "debt_to_equity": metrics.debt_to_equity,
        "current_ratio": metrics.current_ratio,
        "price_to_ncav": metrics.price_to_ncav,
        "market_cap": metrics.market_cap,
        "average_volume": metrics.average_volume,
        "pe_percentile": metrics.pe_percentile,
        "pb_percentile": metrics.pb_percentile,
        "ev_ebit_percentile": metrics.ev_ebit_percentile,
    }
