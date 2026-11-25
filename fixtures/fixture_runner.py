"""Exercise deterministic grading using built-in fictional tasks and OTLP fixtures."""

import argparse
import http.client
import json
import math
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


def normalize_label(value):
    return " ".join(value.strip().casefold().split())


def normalize_label_regression(value):
    return value.lower()


def total_by_key(records):
    totals = {}
    for key, amount in records:
        totals[key] = totals.get(key, 0) + amount
    return totals


def total_by_key_regression(records):
    return dict(records)


@dataclass(frozen=True)
class Task:
    name: str
    checks: tuple
    expected_path: str
    reference: object
    regression: object


TASKS = (
    Task(
        "normalize_label",
        (("  ALPHA  BETA ", "alpha beta"), ("", ""), ("A\t\n Z", "a z"), ("Straße", "strasse")),
        "labels.py",
        normalize_label,
        normalize_label_regression,
    ),
    Task(
        "total_by_key",
        (((), {}), ((('a', 4), ('a', -2), ('b', 3)), {'a': 2, 'b': 3}), ((('a', -2),), {'a': -2})),
        "totals.py",
        total_by_key,
        total_by_key_regression,
    ),
)


@dataclass(frozen=True)
class Attempt:
    task: str
    variant: str
    passed: int
    checks: int
    scope_violations: int
    duration_seconds: float

    @property
    def success(self):
        return int(self.passed == self.checks and self.scope_violations == 0)


def grade(task, variant, function, changed_paths):
    started = time.perf_counter()
    passed = 0
    for input_value, expected in task.checks:
        try:
            actual = function(input_value)
        except Exception:
            continue
        passed += actual == expected
    violations = sum(path != task.expected_path for path in changed_paths)
    return Attempt(task.name, variant, passed, len(task.checks), violations, time.perf_counter() - started)


def evaluate():
    attempts = []
    for task in TASKS:
        attempts.extend((
            grade(task, "reference", task.reference, (task.expected_path,)),
            grade(task, "regression", task.regression, (task.expected_path,)),
            grade(task, "scope_violation", task.reference, (task.expected_path, "unrelated.txt")),
        ))
    return attempts


def verify(attempts):
    for attempt in attempts:
        if not math.isfinite(attempt.duration_seconds) or attempt.duration_seconds < 0:
            raise RuntimeError("Invalid monotonic elapsed time")
        if attempt.variant == "reference" and attempt.success != 1:
            raise RuntimeError(f"Reference failed: {attempt.task}")
        if attempt.variant == "regression" and (attempt.passed >= attempt.checks or attempt.scope_violations):
            raise RuntimeError(f"Functional regression was not isolated: {attempt.task}")
        if attempt.variant == "scope_violation" and (attempt.success or attempt.passed != attempt.checks or attempt.scope_violations != 1):
            raise RuntimeError(f"Scope violation was not isolated: {attempt.task}")


def gauge(name, detail, unit, attempts, value, timestamp):
    return {
        "name": name,
        "description": detail,
        "unit": unit,
        "gauge": {"dataPoints": [
            {
                "attributes": [
                    {"key": "task", "value": {"stringValue": attempt.task}},
                    {"key": "variant", "value": {"stringValue": attempt.variant}},
                ],
                "timeUnixNano": str(timestamp),
                "asDouble": value(attempt),
            }
            for attempt in attempts
        ]},
    }


def payload_for(attempts):
    now = time.time_ns()
    series = (
        ("codex_fixture_attempt_success", "Synthetic attempt passed functional and scope checks", "", lambda a: a.success),
        ("codex_fixture_check_pass_ratio", "Fraction of fixture checks passed", "", lambda a: a.passed / a.checks),
        ("codex_fixture_checks", "Number of fixture checks", "", lambda a: a.checks),
        ("codex_fixture_scope_violations", "Synthetic changed paths outside the task scope", "", lambda a: a.scope_violations),
        ("codex_fixture_runtime", "Time spent executing fixture checks", "s", lambda a: a.duration_seconds),
    )
    return {"resourceMetrics": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "codex-fixture"}}]},
        "scopeMetrics": [{
            "scope": {"name": "codex-observability-fixture"},
            "metrics": [gauge(name, detail, unit, attempts, value, now) for name, detail, unit, value in series],
        }],
    }]}


def emit(endpoint, payload):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "http" or parsed.netloc not in {"127.0.0.1:4318", "otel-collector:4318"}
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Use the loopback endpoint or the Collector on the Compose network")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("POST", "/v1/metrics", json.dumps(payload), {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(8192)
        if response.status != 200:
            raise RuntimeError(f"Collector replied with HTTP {response.status}")
        partial = (json.loads(body) if body else {}).get("partialSuccess", {})
        if partial.get("errorMessage") or int(partial.get("rejectedDataPoints", 0)):
            raise RuntimeError("Collector reported partial rejection of fixture metrics")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test", help="Verify reference, regression, and scope checks offline")
    publisher = commands.add_parser("emit", help="Send only built-in fixture metrics to the local Collector")
    publisher.add_argument("--endpoint", default="http://127.0.0.1:4318")
    arguments = parser.parse_args()
    attempts = evaluate()
    verify(attempts)
    if arguments.command == "emit":
        emit(arguments.endpoint, payload_for(attempts))
    print(json.dumps({
        "mode": "synthetic",
        "published": arguments.command == "emit",
        "attempts": len(attempts),
        "reference_passed": sum(a.success for a in attempts if a.variant == "reference"),
        "negative_cases_detected": sum(not a.success for a in attempts if a.variant != "reference"),
    }))


if __name__ == "__main__":
    main()
