# Development notes

Each evaluator owns one JSON store. Do not share a store between processes.

Dates use ISO output. Numeric birth dates use month/day/year; written months
avoid ambiguity for callers from different locales.

The harness passes correlation metadata through environment variables. Local
traces retain missing fields as null so consumers can distinguish unset values.
