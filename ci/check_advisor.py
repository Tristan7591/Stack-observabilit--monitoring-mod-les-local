"""Inject and check a reproducible advisor finding in the disposable stack."""

import argparse
import http.client
import json
import subprocess
import time


MARKER = "synthetic-advisor-content-that-must-not-be-reported"
ADVISOR = ("docker", "compose", "--profile", "advisor", "exec", "-T", "advisor", "python", "/app/main.py")


def inject(port):
    now = time.time_ns()
    records = []
    for attempt in (0, 1):
        for index in range(12):
            records.append({
                "timeUnixNano": str(now + index + 12 * attempt),
                "body": {"stringValue": MARKER},
                "attributes": [
                    {"key": "event.name", "value": {"stringValue": "codex.api_request"}},
                    {"key": "success", "value": {"boolValue": not attempt}},
                    {"key": "attempt", "value": {"intValue": str(attempt)}},
                    {"key": "arguments", "value": {"stringValue": MARKER}},
                ],
            })
    payload = {"resourceLogs": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "codex-advisor-fixture"}}]},
        "scopeLogs": [{"scope": {"name": "codex-advisor-ci"}, "logRecords": records}],
    }]}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/v1/logs", json.dumps(payload), {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(8192)
        if response.status != 200 or (body and (json.loads(body).get("partialSuccess") or {}).get("rejectedLogRecords")):
            raise SystemExit("Advisor test fixture was rejected.")
    finally:
        connection.close()


def verify():
    deadline = time.monotonic() + 40
    details = {}
    while time.monotonic() < deadline:
        subprocess.run((*ADVISOR, "--once"), check=True, stdout=subprocess.DEVNULL, timeout=45)
        raw = subprocess.check_output((*ADVISOR, "--show-latest"), timeout=10)
        if MARKER.encode() in raw:
            raise SystemExit("Advisor report contains content from a fixture log.")
        report = json.loads(raw)
        observed = report["signals"]
        ids = {item["id"] for item in report["recommendations"]}
        details = {"query_failures": report["query_failures"], "attempts": observed["http_attempts"],
                   "retries": observed["http_retries"], "failures": observed["http_failures"]}
        if (not report["query_failures"] and {"http-failures", "http-retries"} <= ids
                and observed["http_attempts"] == 24 and observed["http_retries"] == 12 and observed["http_failures"] == 12):
            print("Advisor combined Loki attempts and Prometheus health without retaining log content.")
            return
        time.sleep(2)
    raise SystemExit("Unexpected aggregates from the advisor: " + json.dumps(details))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4318)
    inject(parser.parse_args().port)
    verify()
