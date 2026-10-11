# Development notes

Each evaluator owns one JSON store. Do not share a store between processes.

Dates use ISO output. Numeric birth dates use month/day/year; written months
avoid ambiguity for callers from different locales.

The harness passes correlation metadata through environment variables. Local
traces retain missing fields as null so consumers can distinguish unset values.

Write deduplication applies within one conversation only. A new evaluation
creates a new dispatcher and does not inherit any cached writes.

Model prices use USD per million tokens. Provider-reported usage includes
reasoning output where applicable; accounting is cumulative within a run.
