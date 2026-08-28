"""Cost and token instrumentation. OWNER: Lane E.

THERE WAS NO COST TRACKING AT ALL BEFORE THIS -- measured on the pre-final
baseline, `agentorg/common/llm.py` recorded no usage of any kind, so nobody could
answer "what did that run cost". Two judge requirements were unanswerable as a
result: the time-and-cost comparison against a developer driving Claude Code by
hand, and the cost view in the product UI.

Three modules, and the split is by what can be wrong with each:

  * `prices.py`  -- what a token costs. WRONG WHEN STALE, so every entry carries
                    the date it was read and the command that read it.
  * `record.py`  -- what a run consumed. WRONG WHEN IT GUESSES, so an unpriced
                    model produces `usd=None` rather than a confident zero.
  * `report.py`  -- what a reader is told. WRONG WHEN IT FLATTERS, so an
                    unmeasured cache reads as *unmeasured*, never as 0%.

The public surface is deliberately small: `build_cost_record`, `price_stage`,
`total_usd`, `cache_hit_rate` and `render`. Everything else is an implementation
detail of one of the three failures above.
"""

from .prices import (
    PRICES,
    PriceRow,
    price_for,
)
from .record import (
    build_cost_record,
    cache_hit_rate,
    merge_cost_records,
    price_stage,
    total_usd,
)
from .report import render

__all__ = [
    "PRICES",
    "PriceRow",
    "build_cost_record",
    "cache_hit_rate",
    "merge_cost_records",
    "price_for",
    "price_stage",
    "render",
    "total_usd",
]
