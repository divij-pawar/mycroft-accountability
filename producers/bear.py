"""
The bear lens (B4) — the other side of the bull/bear pairing; see producers/bull.py.
Same figures, opposite brief, same grounding rules.
"""

from __future__ import annotations

from core.schemas import AgentID
from producers.bull import SHARED_FIGURES
from producers.lens import ConceptLens

BEAR_LENS = ConceptLens(
    name="bear",
    agent_id=AgentID.BEAR,
    concepts=SHARED_FIGURES,
    header=("Brief: you are the bear analyst. Make the strongest case AGAINST this company that "
            "these figures support, using only these figures. If they do not support a bearish "
            "view, say so plainly."),
    trace_prefix="llm_call_bear",
    version="v1",
)
