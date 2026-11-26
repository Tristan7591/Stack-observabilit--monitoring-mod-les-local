"""Summarize redacted provider attempts and externally verified task outcomes."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys


ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
OUTCOMES = {"success", "failed", "cancelled"}
ATTEMPT_FIELDS = {
    "kind", "task_id", "operation_id", "attempt", "outcome", "input_tokens", "output_tokens", "cached_input_tokens",
}
TASK_FIELDS = {"kind", "task_id", "variant", "passed", "attempts_complete"}


def validate(row, number):
    def require(condition, field):
        if not condition:
            raise ValueError(f"Invalid record on line {number}: {field}")

    require(isinstance(row, dict) and row.get("kind") in ("attempt", "task"), "kind")
    attempt = row["kind"] == "attempt"
    require(row.keys() == (ATTEMPT_FIELDS if attempt else TASK_FIELDS), "missing or unexpected fields")
    for field in ("task_id", "operation_id" if attempt else "variant"):
        require(isinstance(row[field], str) and ID.fullmatch(row[field]), field)
    if not attempt:
        for field in ("passed", "attempts_complete"):
            require(type(row[field]) is bool, field)
        return row

    require(type(row["attempt"]) is int and row["attempt"] > 0, "attempt")
    require(isinstance(row["outcome"], str) and row["outcome"] in OUTCOMES, "outcome")
    for field in ("input_tokens", "output_tokens", "cached_input_tokens"):
        value = row[field]
        require(value is None or (type(value) is int and value >= 0), field)
    cached = row["cached_input_tokens"]
    require(cached is None or (row["input_tokens"] is not None and cached <= row["input_tokens"]), "cached_input_tokens")
    return row


def report(rows):
    tasks = {}
    attempts = defaultdict(list)
    identities = set()
    for number, item in enumerate(rows, start=1):
        row = validate(item, number)
        task_id = row["task_id"]
        if row["kind"] == "task":
            if task_id in tasks:
                raise ValueError(f"Duplicate task on line {number}")
            tasks[task_id] = row
            continue
        identity = (task_id, row["operation_id"], row["attempt"])
        if identity in identities:
            raise ValueError(f"Duplicate attempt on line {number}")
        identities.add(identity)
        attempts[task_id].append(row)
    if not tasks or attempts.keys() - tasks.keys():
        raise ValueError("Provide one task verdict for every recorded attempt")

    groups = defaultdict(list)
    for task_id, task in tasks.items():
        groups[task["variant"]].append((task, attempts[task_id]))
    summaries = {}
    for variant, entries in sorted(groups.items()):
        passed = sum(task["passed"] for task, _ in entries)
        missing_sequences = 0
        all_attempts = [row for _, items in entries for row in items]
        known = [row for row in all_attempts if None not in (row["input_tokens"], row["output_tokens"])]
        for _, items in entries:
            by_operation = defaultdict(list)
            for row in items:
                by_operation[row["operation_id"]].append(row["attempt"])
            missing_sequences += sum(len(indices) != max(indices) for indices in by_operation.values())
        full = all(task["attempts_complete"] and items for task, items in entries)
        full = full and missing_sequences == 0 and len(known) == len(all_attempts)
        total = sum(row["input_tokens"] + row["output_tokens"] for row in known)
        failed = sum(row["input_tokens"] + row["output_tokens"] for row in known if row["outcome"] != "success")
        repeated = sum(row["input_tokens"] for row in all_attempts if row["attempt"] > 1 and row["input_tokens"] is not None)
        cache_pairs = [row for row in all_attempts if row["cached_input_tokens"] is not None]
        cache_eligible = sum(row["input_tokens"] for row in cache_pairs)
        summaries[variant] = {
            "tasks": len(entries),
            "passed_tasks": passed,
            "success_ratio": passed / len(entries),
            "attempts": len(all_attempts),
            "attempts_without_token_total": len(all_attempts) - len(known),
            "operations_with_missing_attempt_numbers": missing_sequences,
            "complete_accounting": full,
            "known_tokens": total,
            "tokens_per_successful_task": total / passed if full and passed else None,
            "known_tokens_on_unsuccessful_attempts": failed,
            "known_input_tokens_on_later_attempts": repeated,
            "cache_reporting_attempts": len(cache_pairs),
            "cached_input_ratio_as_reported": sum(row["cached_input_tokens"] for row in cache_pairs) / cache_eligible if cache_eligible else None,
        }
    return {"variants": summaries}


def read_jsonl(path):
    with path.open(encoding="utf-8-sig") as records:
        for number, line in enumerate(records, 1):
            try:
                yield json.loads(line)
            except ValueError:
                raise ValueError(f"Invalid JSON on line {number}") from None


def example():
    return [
        dict(kind="task", task_id="trial-a", variant="baseline", passed=True, attempts_complete=True),
        dict(kind="attempt", task_id="trial-a", operation_id="fix", attempt=1, outcome="failed", input_tokens=100, output_tokens=30, cached_input_tokens=None),
        dict(kind="attempt", task_id="trial-a", operation_id="fix", attempt=2, outcome="success", input_tokens=150, output_tokens=50, cached_input_tokens=80),
        dict(kind="task", task_id="trial-b", variant="candidate", passed=True, attempts_complete=True),
        dict(kind="attempt", task_id="trial-b", operation_id="fix", attempt=1, outcome="success", input_tokens=120, output_tokens=40, cached_input_tokens=0),
    ]


def self_test():
    rows = example()
    summary = report(rows)["variants"]
    assert summary["baseline"]["tokens_per_successful_task"] == 330
    assert summary["baseline"]["known_tokens_on_unsuccessful_attempts"] == 130
    assert summary["baseline"]["known_input_tokens_on_later_attempts"] == 150
    assert summary["candidate"]["tokens_per_successful_task"] == 160
    assert report(rows[:1] + rows[2:])["variants"]["baseline"]["tokens_per_successful_task"] is None
    rows[1]["output_tokens"] = None
    assert report(rows)["variants"]["baseline"]["attempts_without_token_total"] == 1
    assert report(rows)["variants"]["baseline"]["tokens_per_successful_task"] is None
    for invalid in (rows + [rows[1]], rows + [dict(rows[1], prompt="synthetic")]):
        try:
            report(invalid)
        except ValueError:
            continue
        raise AssertionError("Invalid telemetry was accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--example", action="store_true", help="Print fictional JSONL records")
    source.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("Provider report self-test passed.")
        elif args.example:
            for row in example():
                print(json.dumps(row))
        else:
            print(json.dumps(report(read_jsonl(args.input)), indent=2))
    except (OSError, ValueError) as error:
        sys.exit(str(error) if isinstance(error, ValueError) else "Unable to read the input report file.")


if __name__ == "__main__":
    main()
