"""core — provider- and domain-agnostic numeric primitives shared by all products.

Contains ONLY pure functions over abstract numeric series and scalars: no domain-specific
identifiers, no IO, no LLM, no third-party deps. Domain formulas and product logic live in the
products that import these primitives.
"""
