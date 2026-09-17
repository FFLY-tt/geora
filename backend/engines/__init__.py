"""
Diagnostic engines package.

Each module in this package implements exactly one diagnostic dimension
and exposes a single public function that returns a `DimensionScore`.
No engine module may import from another engine module's internals;
if data needs to be shared (e.g. relevance_engine reusing citation_engine's
raw answers), it must be passed explicitly through function arguments.
"""
