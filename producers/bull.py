"""
The bull lens (B4) — one side of the bull/bear pairing.

Bull and bear read the SAME figures from the one EDGAR fetch: every concept the
financial and earnings lenses read, together. They differ only in their brief. That
is the opposite of the financial/earnings pairing, where the lenses differ in what
they see. Here disagreement can only come from interpretation, which is what B5 is
meant to classify: the same facts with different stated assumptions.

The brief asks for the strongest case the figures actually support. It never
permits going beyond them: the directive's GROUNDING RULE still binds every
number, and "the figures don't support a bullish view" is an acceptable answer.
"""

from __future__ import annotations

from core.schemas import AgentID
from producers.earnings import EARNINGS_LENS
from producers.financial import FINANCIAL_LENS
from producers.lens import ConceptLens

# Every concept either filing lens reads, in first-seen order: both sides see everything.
SHARED_FIGURES: tuple[str, ...] = tuple(dict.fromkeys(FINANCIAL_LENS.concepts + EARNINGS_LENS.concepts))

BULL_LENS = ConceptLens(
    name="bull",
    agent_id=AgentID.BULL,
    concepts=SHARED_FIGURES,
    header=("Brief: you are the bull analyst. Make the strongest case FOR this company that these "
            "figures support, using only these figures. If they do not support a bullish view, "
            "say so plainly."),
    trace_prefix="llm_call_bull",
    version="v1",
)
