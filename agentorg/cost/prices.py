"""The price table. One row per model, each carrying the date it was read.

WHY EVERY ROW CARRIES A DATE. Prices change, an undated table cannot be audited,
and the failure mode is specific and bad: the presenter quotes a number to a
judge, the judge is holding today's pricing page, and the two disagree with
nothing on the slide to say which is older. A dated row makes "this is what it
cost on 2026-08-28" a defensible sentence; an undated one makes every figure a
claim about the present that nobody checked.

Rates are per MILLION tokens, in USD. The AWS Pricing API quotes per 1K, and the
conversion is done here, once, rather than at three call sites -- see
`_per_million`.

── HOW THESE NUMBERS WERE MEASURED, so the next person can re-measure them ──

Not read off a web page. The AWS Pricing API is the authoritative source and it
is queryable, which makes this table reproducible:

    aws pricing get-products --service-code AmazonBedrock --region us-east-1 \\
      --filters "Type=TERM_MATCH,Field=model,Value=Nova 2.0 Lite" \\
                "Type=TERM_MATCH,Field=regionCode,Value=us-east-1" \\
                "Type=TERM_MATCH,Field=feature,Value=On-demand Inference"

Measured output, 2026-08-28 (publicationDate 2026-08-26, effectiveDate
2026-08-01), the STANDARD tier rows only:

    Nova 2.0 Lite  Input tokens                     0.00033   / 1K = $0.3300 /1M
    Nova 2.0 Lite  Output tokens                    0.00275   / 1K = $2.7500 /1M
    Nova 2.0 Lite  Prompt cache read input tokens   0.0000825 / 1K = $0.0825 /1M
    Nova 2.0 Lite  Prompt cache write input tokens  0.0        / 1K = $0.0000 /1M

    Nova Lite      Input tokens                     0.00006   / 1K = $0.0600 /1M
    Nova Lite      Output tokens                    0.00024   / 1K = $0.2400 /1M
    Nova Lite      Prompt cache read input tokens   0.000015  / 1K = $0.0150 /1M
    Nova Lite      Prompt cache write input tokens  0.0        / 1K = $0.0000 /1M

TWO TRAPS THE QUERY ITSELF EXPOSED, both worth knowing before re-measuring:

1. THE PRICING API'S NAME FOR OUR MODEL IS "Nova 2.0 Lite", NOT "Nova 2 Lite".
   `config.BEDROCK_MODEL` is `us.amazon.nova-2-lite-v1:0`; the pricing catalogue
   spells the same model `Nova 2.0 Lite`. A query for `Nova 2 Lite` returns ZERO
   rows and exits 0, which reads exactly like a model with no pricing.
   `aws pricing get-attribute-values --attribute-name model` lists the real names.

2. `Nova Lite` AND `Nova 2.0 Lite` ARE DIFFERENT MODELS AT 5.5x AND 11x THE
   PRICE. Reading the older row for the newer model understates output cost by
   more than an order of magnitude. Both are in the table below so the mistake is
   visible rather than silent.

Also measured, and the reason the flex/priority tiers are NOT in this table: the
same query returns `Input tokens flex` at half the standard rate and
`Input tokens priority` at 1.75x. This pipeline uses neither -- it calls
`invoke_agent_runtime` on a default on-demand runtime -- so pricing a run at the
flex rate would halve every reported figure for a tier nobody selected.

── WHAT IS DELIBERATELY ABSENT ──

Anthropic first-party models. They belong in the req-2 comparison (a developer
driving Claude Code by hand), and their rates are published per model on
Anthropic's own pricing page rather than in the AWS catalogue -- a different
source, a different date, and a different billing account. Adding them here from
recall is precisely what this module's date field exists to prevent; adding them
from a measured source is a one-row change when somebody has that source in
front of them. `price_for` already answers None for them, and None is the honest
answer for a model this table has never been told about.
"""

from __future__ import annotations

from dataclasses import dataclass

# The AWS Pricing API quotes `1K tokens`. Everything downstream reasons in
# millions, because that is the unit every provider's public pricing page uses and
# the unit a slide can carry without four leading zeros.
_PER_MILLION = 1000


def _per_million(per_1k: float) -> float:
    """Convert an AWS `1K tokens` rate to a per-million rate.

    Exists so the factor appears ONCE. Written inline at three call sites, a typo
    in one of them would be a 1000x error in a single column -- and a cost that is
    wrong by 1000x in one column still looks like a plausible dollar figure.
    """
    return per_1k * _PER_MILLION


@dataclass(frozen=True)
class PriceRow:
    """What one model costs, and WHEN THAT WAS TRUE.

    `read_on` is not decoration and it is not optional. A price table without it
    cannot answer the only question that matters when a figure is challenged --
    "is this current?" -- and the absence of an answer there is what turns a
    measured number back into a guess.

    `source` names the command, not the vendor. "AWS" would be useless to somebody
    trying to reproduce the row; the CLI invocation is runnable.

    Frozen because a price is a fact about a date. Code that wants a different
    price wants a different ROW, and mutating this one would silently reprice runs
    that were already reported.
    """

    model: str                  # the model id as the SDK names it
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    read_on: str                # ISO date. See the docstring.
    source: str                 # the command that produced the numbers
    catalogue_name: str = ""    # the pricing API's own name, when it differs


# The command every row below was read with, kept beside the rows so it cannot
# drift from them. `--filters` omitted for brevity; the full invocation is in the
# module docstring.
_AWS = "aws pricing get-products --service-code AmazonBedrock --region us-east-1"

# The date the rows were read. One constant rather than three copies of a string,
# because three copies drift and a row claiming the wrong date is worse than a row
# claiming none: it reads as having been checked.
_READ_ON = "2026-08-28"

PRICES: dict[str, PriceRow] = {
    # THE MODEL THIS REPOSITORY ACTUALLY CALLS. `config.BEDROCK_MODEL` and
    # `config.LLM_MODEL` both default to this id. The `us.` prefix makes it a
    # cross-region inference profile rather than a foundation model -- which
    # matters enormously for IAM and not at all for price, since the catalogue
    # prices the model, not the ARN shape.
    "us.amazon.nova-2-lite-v1:0": PriceRow(
        model="us.amazon.nova-2-lite-v1:0",
        input_per_million=_per_million(0.00033),
        output_per_million=_per_million(0.00275),
        cache_read_per_million=_per_million(0.0000825),
        read_on=_READ_ON,
        source=_AWS,
        catalogue_name="Nova 2.0 Lite",
    ),
    # The same model without the inference-profile prefix. Present because a
    # future config change to the bare id must not silently become an unpriced
    # run, and because the two ids are one keystroke apart.
    "amazon.nova-2-lite-v1:0": PriceRow(
        model="amazon.nova-2-lite-v1:0",
        input_per_million=_per_million(0.00033),
        output_per_million=_per_million(0.00275),
        cache_read_per_million=_per_million(0.0000825),
        read_on=_READ_ON,
        source=_AWS,
        catalogue_name="Nova 2.0 Lite",
    ),
    # THE OLDER, CHEAPER MODEL -- 5.5x less on input and 11x less on output. In the
    # table so that reading the wrong row is a visible mistake rather than a
    # plausible one. See trap 2 in the module docstring.
    "us.amazon.nova-lite-v1:0": PriceRow(
        model="us.amazon.nova-lite-v1:0",
        input_per_million=_per_million(0.00006),
        output_per_million=_per_million(0.00024),
        cache_read_per_million=_per_million(0.000015),
        read_on=_READ_ON,
        source=_AWS,
        catalogue_name="Nova Lite",
    ),
    "amazon.nova-lite-v1:0": PriceRow(
        model="amazon.nova-lite-v1:0",
        input_per_million=_per_million(0.00006),
        output_per_million=_per_million(0.00024),
        cache_read_per_million=_per_million(0.000015),
        read_on=_READ_ON,
        source=_AWS,
        catalogue_name="Nova Lite",
    ),
}


def price_for(model: str) -> PriceRow | None:
    """The row for `model`, or None when this table has never been told about it.

    NONE IS A REAL ANSWER AND IT MUST STAY REACHABLE. It is what makes
    `CostRecord.usd is None` mean "not priced" -- an unknown model, or a table
    nobody updated -- as distinct from `0.0`, which means priced and free.

    So there is deliberately NO fallback row and NO prefix matching. A
    `nova-3-lite` that quietly matched the nova-2 row would report a confident
    figure computed from the wrong model's rates, and nothing downstream could
    tell. An exact-match miss is loud in the only way that helps: the cost comes
    back unpriced, and `report.render` says so in words.
    """
    return PRICES.get(model)
