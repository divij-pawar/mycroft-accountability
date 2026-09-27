"""
Agent adapters — one provider each, all satisfying the same contract.

The contract itself is `core.contracts.AgentAdapter`:
`(subject, context, directive) -> AgentResponse`, raising
`core.parsing.StructuralParseError` when the model's output does not satisfy the
structural contract. It used to be described only in a comment here, which meant
nothing could be annotated against it; it is now a named `Protocol` that every
factory in this package declares as its return type.

`adapters.registry` maps provider names to those factories, so callers select a
provider by name without importing any of these modules directly.
"""
