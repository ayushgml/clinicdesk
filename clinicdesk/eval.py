"""One task, one JSON line; detailed local traces never go to stdout."""
import argparse
import contextlib
import io
import json
import os
import time
import uuid
from pathlib import Path
from .agent import Agent
from .checks import check_task
from .llm import LLM
from .store import Store
from .tools import DeskTools


def load_task(task_id: str, root: Path | None = None):
    root = root or Path(__file__).resolve().parents[1] / "evals"
    if not task_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in task_id):
        raise ValueError("Invalid task ID")
    matches = [p for p in (root / f"{task_id}.json", root / "heldout" / f"{task_id}.json") if p.is_file()]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous task: {task_id}")
    task = json.loads(matches[0].read_text())
    if task["id"] != task_id:
        raise ValueError("Task ID does not match file")
    return task


def run_task(task, seed: int, llm=None, trace_root: Path | None = None):
    start = time.perf_counter()
    trace_id = str(uuid.uuid4())
    root = trace_root or Path(os.getenv("CLINICDESK_TRACE_DIR", ".clinicdesk/traces"))
    directory = root / trace_id
    result = {"passed": False, "cost": 0.0, "latency": 0.0, "tokens": {"input": 0, "output": 0}, "trace_id": trace_id, "error": None}
    agent = tools = None
    failures = []
    try:
        directory.mkdir(parents=True, exist_ok=False)
        store = Store(directory / "store.json", task["initial_store"])
        tools = DeskTools(store, task["id"], seed, task.get("faults"))
        llm = llm or LLM()
        agent = Agent(llm, tools)
        for message in task["messages"]:
            agent.turn(message)
        failures = check_task(task, store.data, tools.log, agent.responses)
        result["passed"] = not failures
        if failures:
            result["error"] = f"{len(failures)} deterministic check(s) failed"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if llm:
            result["cost"] = llm.cost if llm.cost_known else None
            result["tokens"] = {"input": llm.input_tokens, "output": llm.output_tokens}
        result["latency"] = round(time.perf_counter() - start, 6)
        trace = {"trace_id": trace_id, "task_id": task["id"], "seed": seed,
                 "metadata": {k: os.getenv(k) for k in ("CULPRIT_RUN_ID", "COMMIT_SHA", "TASK_ID", "REPETITION")},
                 "result": result, "failed_checks": failures, "tool_log": tools.log if tools else [],
                 "model_log": agent.model_log if agent else [], "responses": agent.responses if agent else [],
                 "usage": llm.calls if llm else []}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
        except OSError:
            result["trace_id"] = None
    return result


def main():
    started = time.perf_counter()
    try:
        # Also capture argparse diagnostics so stdout always has exactly one record.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            parser = argparse.ArgumentParser()
            parser.add_argument("--task", required=True)
            parser.add_argument("--seed", type=int, required=True)
            args = parser.parse_args()
            result = run_task(load_task(args.task), args.seed)
    except (Exception, SystemExit) as exc:
        result = {"passed": False, "cost": 0.0, "latency": time.perf_counter() - started,
                  "tokens": {"input": 0, "output": 0}, "trace_id": None, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))

if __name__ == "__main__":
    main()
