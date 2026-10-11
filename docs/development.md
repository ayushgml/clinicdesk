# Development notes

Each evaluator owns one JSON store. Do not share a store between processes.

Dates use ISO output. Numeric birth dates use month/day/year; written months
avoid ambiguity for callers from different locales.
