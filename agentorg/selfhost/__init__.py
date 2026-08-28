"""Running this pipeline on your own compute. OWNER: Lane F.

The seam this lane needed ALREADY EXISTED. `config.LLM_BASE_URL` routes every
agent to an OpenAI-compatible gateway instead of Bedrock, and the three scanners
have always run as local binaries in our own container. So this package does not
build a self-hosted mode; it PROVES one, and it names what degrades.

Two modules, split by what can be wrong with each:

  `airgap.py`   wrong when it reports an absence it did not verify
  `parity.py`   wrong when it flatters the local model

Nothing here is imported by `agentorg/graph.py`, `scripts/run_stage.py` or any
agent. That is deliberate: an evidence-gathering package on the pipeline path is
one import away from becoming a dependency of the thing it measures.
"""

from __future__ import annotations

from .airgap import (
    AWS_HOST_MARKERS,
    ContactedAWS,
    NetworkWitness,
    witness,
)
from .parity import (
    INVARIANT_COLUMN,
    ParityRow,
    ParitySet,
    RunObservation,
    compare,
    render_parity_table,
)

__all__ = [
    "AWS_HOST_MARKERS",
    "INVARIANT_COLUMN",
    "ContactedAWS",
    "NetworkWitness",
    "ParityRow",
    "ParitySet",
    "RunObservation",
    "compare",
    "render_parity_table",
    "witness",
]
