"""Generate a local advice report from fixed Prometheus and Loki aggregate queries."""

import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlencode

from engine import recommend


REPORT = Path("/reports/latest.json")
SOURCES = {
    "prometheus": ("prometheus", 9090, "/api/v1/query"),
    "loki": ("loki", 3100, "/loki/api/v1/query"),
}
CODEX_LOGS = '{service_name=~".*[Cc]odex.*",service_name!~"codex-smoke|codex-provider-test|codex-fixture|codex-network-probe"}'
API_LOGS = CODEX_LOGS + ' |= "codex.api_request" | event_name="codex.api_request"'
REFUSAL_SIGNALS = ("spans", "metric_points", "log_records")


def queries(minutes):
    window = f"[{minutes}m]"

    def increase(metric, labels=""):
        return f'sum(increase({{job="codex-otlp",__name__=~"{metric}"{labels}}}{window}))'

    def count_log(selector):
        return f"sum(count_over_time({selector}{window}))"

    def token_sum(token_type):
        return increase("codex_turn_token_usage(_tokens)?_sum", f',token_type="{token_type}"')

    def refusals(signal):
        metric = f"otelcol_receiver_refused_{signal}"
        selector = '{job="otel-collector"}'
        return f"sum(increase({metric}_total{selector}{window})) or sum(increase({metric}{selector}{window}))"

    outcomes = ' | success=~"true|false"'
    attempts = ' | attempt >= 0 | __error__=""'
    continuation = "codex_websocket_continuation_total"
    duration = 'job="codex-otlp",__name__=~"codex_turn_e2e_duration_ms(_milliseconds)?_bucket"'
    return {
        "targets_down": ("prometheus", "sum(up == bool 0)"),
        "host_sample_age_seconds": ("prometheus", "clamp_min(time() - max(codex_host_collector_last_success_timestamp_seconds), 0)"),
        **{f"collector_refused_{signal}": ("prometheus", refusals(signal)) for signal in REFUSAL_SIGNALS},
        "http_attempts": ("loki", count_log(API_LOGS + outcomes + attempts)),
        "http_retries": ("loki", count_log(API_LOGS + outcomes + ' | attempt > 0 | __error__=""')),
        "http_failures": ("loki", count_log(API_LOGS + ' | success="false"' + attempts)),
        "ws_sends": ("prometheus", increase(continuation, ',phase="generation"')),
        "ws_full_resends": ("prometheus", increase(continuation, ',phase="generation",mode="full",reason!~"no_previous_request|restored_history"')),
        "completion_events": ("loki", count_log(CODEX_LOGS + ' |= "codex.sse_event" | event_name="codex.sse_event" | event_kind="response.completed" | input_token_count >= 0 | __error__=""')),
        "input_tokens": ("prometheus", token_sum("input")),
        "cached_input_tokens": ("prometheus", token_sum("cached_input")),
        "failed_tool_calls": ("prometheus", increase("codex_tool_call_total", ',success="false"')),
        "tool_calls": ("prometheus", increase("codex_tool_call_total", ',success=~"true|false"')),
        "turn_count": ("prometheus", increase("codex_turn_e2e_duration_ms(_milliseconds)?_count")),
        "turn_p95_seconds": ("prometheus", f"histogram_quantile(0.95, sum by (le) (increase({{{duration}}}{window}))) / 1000"),
    }


def scalar(backend, expression, end):
    host, port, path = SOURCES[backend]
    instant = str(end) if backend == "loki" else str(end / 1_000_000_000)
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request("GET", f"{path}?{urlencode({'query': expression, 'time': instant})}")
        response = connection.getresponse()
        payload = response.read(65537)
        if response.status != 200 or len(payload) > 65536:
            raise ValueError("Backend status or aggregate response size")
        document = json.loads(payload)
        if document.get("status") != "success" or document["data"]["resultType"] != "vector":
            raise ValueError("Invalid aggregate response")
        result = document["data"]["result"]
        if not result:
            return None
        if len(result) != 1:
            raise ValueError("Expected exactly one aggregate")
        value = float(result[0]["value"][1])
        return value if math.isfinite(value) else None
    finally:
        connection.close()


def build_report(minutes, fetch=scalar):
    end = time.time_ns()
    snapshot, failures = {}, []
    for name, (source, expression) in queries(minutes).items():
        try:
            snapshot[name] = fetch(source, expression, end)
        except (OSError, http.client.HTTPException, KeyError, IndexError, TypeError, ValueError):
            snapshot[name] = None
            failures.append(name)
    refused = [snapshot.pop(f"collector_refused_{signal}") for signal in REFUSAL_SIGNALS]
    observed_refusals = [value for value in refused if value is not None]
    snapshot["collector_rejected"] = sum(observed_refusals) if observed_refusals else None
    # Only zero-fill side counts when the same source produced a positive denominator.
    for numerator, denominator in (("http_retries", "http_attempts"), ("http_failures", "http_attempts"),
                                   ("ws_full_resends", "ws_sends"), ("failed_tool_calls", "tool_calls")):
        if snapshot[numerator] is None and numerator not in failures and (snapshot[denominator] or 0) > 0:
            snapshot[numerator] = 0

    return {
        "schema_version": 1,
        "generated_at": datetime.fromtimestamp(end / 1e9, timezone.utc).isoformat(),
        "window_minutes": minutes,
        "query_failures": failures,
        "signals": snapshot,
        "recommendations": recommend(snapshot),
    }


def store(report):
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=REPORT.parent, delete=False)
    staging = Path(stream.name)
    try:
        with stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        staging.replace(REPORT)
    finally:
        staging.unlink(missing_ok=True)


def setting(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        raise SystemExit(f"Invalid {name}") from None
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}")
    return value


def self_test():
    observed = set()

    def mock(source, expression, end):
        observed.add(source)
        assert end > 0
        if "host_collector_last_success" in expression:
            raise OSError("synthetic")
        return 2 if expression == "sum(up == bool 0)" else None

    report = build_report(30, mock)
    assert observed == set(SOURCES)
    assert report["signals"]["targets_down"] == 2
    assert report["query_failures"] == ["host_sample_age_seconds"]
    assert report["recommendations"]
    print("Advisor self-test passed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--show-latest", action="store_true")
    mode.add_argument("--health", action="store_true")
    mode.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.show_latest:
        try:
            print(REPORT.read_text(encoding="utf-8"), end="")
        except OSError:
            raise SystemExit("No advisor report yet.") from None
        return
    interval = setting("ADVISOR_INTERVAL_SECONDS", 300, 30, 3600)
    if args.health:
        try:
            age = time.time() - REPORT.stat().st_mtime
        except OSError:
            raise SystemExit(1) from None
        raise SystemExit(0 if 0 <= age < max(90, 3 * interval) else 1)
    minutes = setting("ADVISOR_WINDOW_MINUTES", 30, 5, 120)
    while True:
        report = build_report(minutes)
        store(report)
        print(json.dumps({"advisor": {
            "generated_at": report["generated_at"],
            "recommendation_ids": [item["id"] for item in report["recommendations"]],
            "query_failures": report["query_failures"],
        }}), flush=True)
        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
