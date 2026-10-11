# clinicdesk

A Python 3.12 clinic front-desk agent over synthetic local JSON records. It schedules,
cancels, routes urgent requests to a mock nurse, and sends mock confirmations. It
provides no medical advice and has no connection to real patients or services.

## Setup

```sh
uv sync --locked
export OPENROUTER_API_KEY=...
export LLM_PROVIDER=openrouter
export VICTIM_MODEL=z-ai/glm-5.3
uv run --locked python -m clinicdesk.eval --task t01 --seed 0
uv run --locked pytest
```

The interpreter in the activated environment also supports
`python -m clinicdesk.eval --task t01 --seed 0`.

With no provider override, `ANTHROPIC_API_KEY` selects Anthropic; otherwise the agent
uses OpenRouter at `https://openrouter.ai/api/v1` via the OpenAI SDK.
`LLM_PROVIDER` accepts `anthropic` or `openrouter`. `VICTIM_MODEL` selects the model.
Temperature is zero. A provider rejection specifically mentioning temperature gets
one retry without that parameter. SDK network retries are disabled.

Usage is accumulated across all model calls, including unsuccessful evaluations.
OpenRouter's reported cost is preferred; otherwise the price table in
`clinicdesk/llm.py` supplies USD per million tokens. Set both `VICTIM_PRICE_IN` and
`VICTIM_PRICE_OUT` to override table prices. An unknown price or potentially billed
transport failure produces `cost: null`, not a fabricated zero. No API call means
zero cost. Prices are estimates when the provider does not report actual billing.

## Evaluation contract

There are 30 public tasks (`t01`–`t30`) and six heldout tasks (`h01`–`h06`). The first
ten public tasks are easy; the others cover indirect urgent symptoms, conversation
context, identity, conflicting appointments, and private schedule annotations.
Each JSON file contains messages, a complete initial store, optional tool faults,
and deterministic assertions. Heldout files are kept separate from public tasks.

The evaluator prints exactly one JSON line and exits zero even for failed tasks:

```json
{"passed":true,"cost":0.001,"latency":1.2,"tokens":{"input":1000,"output":200},"trace_id":"uuid","error":null}
```

These numbers illustrate the schema; they are not measured results. `passed` is a
boolean, `cost` is USD or null, `latency` is seconds, and both token counts are
integers. `trace_id` and `error` are strings or null. Unknown task IDs, malformed
arguments, provider errors, exhausted tool retries, and check failures return a
failed record. No LLM judges the outcome.

Traces and final stores are written under `.clinicdesk/traces/<trace_id>/`;
`CLINICDESK_TRACE_DIR` overrides that directory. Traces include model responses,
filtered tool observations, all failed attempts, check failures, usage, and the
optional `CULPRIT_RUN_ID`, `COMMIT_SHA`, `TASK_ID`, and `REPETITION` environment
metadata. They are ignored by Git. Use only mock data; traces contain conversation
text. No tracing service or tracing key is required.

## Runtime behavior

The model plans ordered tool actions in strict JSON. `$patient` resolves only from
a successful identity lookup; `$appointment` resolves the last new booking.
Supplied name and birth date are verified through the same retrying tool boundary
before planning. Invalid plans receive a bounded correction opportunity. When the
action limit is reached, the agent returns an administrative handoff; checks still
require the requested state changes and confirmations. The conversation is a
single request across turns and permits at most one new booking. Booking conflicts
use exact slot equality; all synthetic slots have the same duration.

The tools are `find_patient`, `get_availability`, `book_appointment`,
`cancel_appointment`, `escalate_to_nurse`, and `send_message`. Their strict argument
schemas live in `schema.py`. Code supplies urgent-symptom facts, verifies caller
credentials, guards bookings, filters other patients' records from results, and
validates service payloads. Each transient tool failure permits up to three retries
(four attempts total). Successful writes are idempotent within a conversation.

Faults are injected before mutation, deterministically from task ID, integer seed,
tool name, and attempt. A task can specify a failure count and timeout, failed, or
malformed result kinds. Failed transactions restore in-memory state. The local JSON
store uses atomic file replacement and is intended for one process per store.
Each evaluation uses its own store directory, so parallel evaluations are isolated.

Date-of-birth parsing accepts ISO, US numeric, and written month formats; ambiguous
numeric input follows US month/day/year. Third-party requests require a separate
authorization workflow, which this mock deliberately does not implement. Emergency
routing may use `unverified` without exposing patient data. Keyword routing is a
benchmark fixture, not clinical triage software.
