"""
The financial agent: a user-facing analyst that the verification layer checks.

A separate component from the verification layer (financial_agent/PLAN.md, D1). It may import
core/, adapters/, pipeline/ and datasources/, and never validation/ or web/
(tests/test_layering.py). The two share only the record schema in core/analysis.py.
"""
