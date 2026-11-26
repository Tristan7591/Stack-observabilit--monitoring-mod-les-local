"""Measure one unauthenticated HTTPS probe to the OpenAI API and optionally emit local OTLP."""

import argparse
import http.client
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid


PROVIDER_URL = "https://api.openai.com/v1/models"
WRITE_OUT = "%{response_code},%{num_connects},%{time_namelookup},%{time_connect},%{time_appconnect},%{time_starttransfer},%{time_total}"
RESOURCE = {"attributes": [{"key": "service.name", "value": {"stringValue": "codex-network-probe"}}]}
SCOPE = {"name": "codex-observability-network-probe"}
PHASES = ("name_lookup", "tcp_connect", "tls_handshake", "time_to_first_byte", "total")


def measurement(output, exit_code):
    status, connections, dns, connect, tls, first_byte, total = output.strip().split(",")
    status, connections = int(status), int(connections)
    dns, connect, tls, first_byte, total = map(float, (dns, connect, tls, first_byte, total))
    if not all(math.isfinite(value) and value >= 0 for value in (dns, connect, tls, first_byte, total)):
        raise ValueError("curl reported invalid timing data")
    phases = {"total": total}
    if dns > 0:
        phases["name_lookup"] = dns
    if connections > 0 and connect > 0 and connect >= dns:
        phases["tcp_connect"] = connect - dns
    if connections > 0 and tls > 0 and tls >= connect:
        phases["tls_handshake"] = tls - connect
    if first_byte > 0:
        phases["time_to_first_byte"] = first_byte
    return {"provider": "openai", "http_status": status or None, "curl_exit_code": exit_code, "seconds": phases}


def payloads(sample, start, end, trace_id):
    def phase_label(phase):
        return [{"key": "phase", "value": {"stringValue": phase}}]

    def gauge(name, entries, unit=""):
        return {
            "name": name, "unit": unit, "gauge": {"dataPoints": [
                {"timeUnixNano": str(end), "asDouble": value, "attributes": labels} for value, labels in entries
            ]},
        }

    metrics = [
        gauge("codex_provider_probe_duration", [
            (duration, phase_label(phase))
            for phase, duration in sample["seconds"].items()
        ], "s"),
        gauge("codex_provider_probe_phase_available", [
            (int(phase in sample["seconds"]), phase_label(phase)) for phase in PHASES
        ]),
        gauge("codex_provider_probe_http_status", [(sample["http_status"] or 0, [])]),
        gauge("codex_provider_probe_curl_exit_code", [(sample["curl_exit_code"], [])]),
        gauge("codex_provider_probe_last_timestamp", [(end / 1e9, [])], "s"),
    ]
    span = {
        "traceId": trace_id, "spanId": uuid.uuid4().hex[:16], "name": "provider.network.probe", "kind": 3,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
        "attributes": [
            {"key": "gen_ai.provider.name", "value": {"stringValue": "openai"}},
            {"key": "http.request.method", "value": {"stringValue": "HEAD"}},
            {"key": "network.probe.exit_code", "value": {"intValue": str(sample["curl_exit_code"])}},
        ] + [
            {"key": f"network.probe.{phase}_ms", "value": {"doubleValue": seconds * 1000}}
            for phase, seconds in sample["seconds"].items()
        ],
    }
    if sample["http_status"] is not None:
        span["attributes"].append({"key": "http.response.status_code", "value": {"intValue": str(sample["http_status"])}})
    if sample["curl_exit_code"]:
        span["status"] = {"code": 2}
    return {
        "metrics": {"resourceMetrics": [{"resource": RESOURCE, "scopeMetrics": [{"scope": SCOPE, "metrics": metrics}]}]},
        "traces": {"resourceSpans": [{"resource": RESOURCE, "scopeSpans": [{"scope": SCOPE, "spans": [span]}]}]},
    }


def emit(signals):
    connection = http.client.HTTPConnection("127.0.0.1", 4318, timeout=5)
    try:
        for signal, payload in signals.items():
            headers = {"Content-Type": "application/json"}
            connection.request("POST", f"/v1/{signal}", json.dumps(payload), headers)
            response = connection.getresponse()
            body = response.read(8192)
            if response.status != 200:
                raise ValueError(f"Local OTLP {signal} export returned HTTP {response.status}")
            partial = (json.loads(body) if body else {}).get("partialSuccess", {})
            if partial.get("errorMessage") or any(int(partial.get(key, 0)) for key in ("rejectedSpans", "rejectedDataPoints")):
                raise ValueError(f"The local Collector partially rejected the {signal} probe")
    finally:
        connection.close()


def self_test():
    complete = measurement("401,1,0.01,0.04,0.09,0.18,0.19", 0)
    assert complete["http_status"] == 401
    assert math.isclose(complete["seconds"]["tcp_connect"], 0.03)
    assert math.isclose(complete["seconds"]["tls_handshake"], 0.05)
    unavailable = measurement("0,0,0,0,0,0,5.01", 28)
    assert unavailable["http_status"] is None and unavailable["seconds"] == {"total": 5.01}
    signals = payloads(complete, 100, 200, "a" * 32)
    span = signals["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["traceId"] == "a" * 32 and "status" not in span
    assert PROVIDER_URL not in json.dumps(signals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-otlp", action="store_true", help="Publish timing and span only to the local Collector")
    parser.add_argument("--self-test", action="store_true", help="Exercise the code offline without contacting any endpoint")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("Provider network probe self-test passed.")
        return

    curl = shutil.which("curl.exe" if os.name == "nt" else "curl")
    if curl is None:
        sys.exit("curl was not found")
    try:
        start = time.time_ns()
        response = subprocess.run([
            curl, "--disable", "--silent", "--head", "--output", os.devnull, "--proto", "=https",
            "--connect-timeout", "5", "--max-time", "12", "--write-out", WRITE_OUT, PROVIDER_URL,
        ], capture_output=True, text=True, timeout=15)
        end = max(start + 1, time.time_ns())
        sample = measurement(response.stdout, response.returncode)
        if args.emit_otlp:
            trace_id = uuid.uuid4().hex
            emit(payloads(sample, start, end, trace_id))
            sample["trace_id"] = trace_id
    except (OSError, http.client.HTTPException, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(f"Probe or local export failed ({type(error).__name__}).")
    print(json.dumps(sample, indent=2))
    sys.exit(response.returncode)


if __name__ == "__main__":
    main()
