"""
Producers — the agents whose disagreement Cross-Agent Validation measures.

Each producer is a `ConceptLens` (see `producers.lens`): a concept set, an
`AgentID`, and nothing else. The registry below is what lets callers enumerate
the available producers instead of hard-coding two names, which is how the UI's
setup band stopped hard-coding the concept lists it displays.
"""

from producers.bear import BEAR_LENS
from producers.bull import BULL_LENS
from producers.earnings import EARNINGS_LENS
from producers.financial import FINANCIAL_LENS
from producers.lens import ConceptLens, run_lens

# Ordered by their role in Cross-Agent Validation: Producer A, then Producer B; then the
# bull/bear pairing (B4), which reads the same figures with opposite briefs.
LENSES: tuple[ConceptLens, ...] = (FINANCIAL_LENS, EARNINGS_LENS, BULL_LENS, BEAR_LENS)

# The pairings /api/compare offers for a ticker: (agent A's lens, agent B's lens).
PAIRINGS: dict[str, tuple[ConceptLens, ConceptLens]] = {
    "lenses": (FINANCIAL_LENS, EARNINGS_LENS),
    "bull_bear": (BULL_LENS, BEAR_LENS),
}

BY_NAME: dict[str, ConceptLens] = {lens.name: lens for lens in LENSES}

__all__ = ["ConceptLens", "run_lens", "FINANCIAL_LENS", "EARNINGS_LENS", "BULL_LENS", "BEAR_LENS",
           "LENSES", "PAIRINGS", "BY_NAME"]
