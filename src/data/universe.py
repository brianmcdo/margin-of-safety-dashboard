"""Ticker universes. The sample list is for a fast, repeatable run."""

from __future__ import annotations

SAMPLE_UNIVERSE: list[str] = [
    "INTC",
    "PFE",
    "BMY",
    "VZ",
    "T",
    "BAC",
    "C",
    "WFC",
    "GM",
    "F",
    "HPQ",
    "IBM",
    "CVS",
    "KHC",
    "MO",
    "LYB",
    "DOW",
    "NUE",
    "AAPL",
    "MSFT",
    "JNJ",
    "KO",
    "PEP",
    "WMT",
    "XOM",
    "CVX",
    "COP",
    "EOG",
    "DVN",
    "SLB",
    "NEM",
    "GOLD",
    "FCX",
    "VALE",
    "BHP",
    "RIO",
]


def normalize_ticker(symbol: str) -> str:
    """Yahoo uses dashes for class shares (BRK-B), Wikipedia uses dots."""
    cleaned = symbol.strip().upper().replace(".", "-")
    return cleaned
