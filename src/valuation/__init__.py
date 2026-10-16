"""Public valuation methods.

Graham and NCAV helpers return ``(value_per_share, note)``. The DCF range
returns ``DcfRange`` with low, base, and high values. A missing value is
None, and the note says why.
"""

from src.valuation.dcf import (
    DcfRange,
    conservative_dcf_per_share,
    conservative_dcf_range,
    normalized_free_cash_flow,
)
from src.valuation.graham import graham_formula_value, graham_number
from src.valuation.ncav import NcavResult, ncav_per_share, net_current_asset_value, strict_ncav_per_share

__all__ = [
    "DcfRange",
    "NcavResult",
    "conservative_dcf_per_share",
    "conservative_dcf_range",
    "graham_formula_value",
    "graham_number",
    "ncav_per_share",
    "net_current_asset_value",
    "normalized_free_cash_flow",
    "strict_ncav_per_share",
]
