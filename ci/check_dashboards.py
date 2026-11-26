"""Validate dashboard structure and exercise provisioned Prometheus queries in CI."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import runpy
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


GRAFANA = "http://127.0.0.1:3000"
TEMPLATE_VALUES = {
    "${process:regex}": ".*",
    "${model:regex}": ".*",
    "$model": ".*",
    "$__rate_interval": "2m",
    "$__range": "3h",
    "$__interval": "30s",
    "$latency_window": "15m",
}


def read_dashboards():
    dashboards = {}
    for path in sorted(Path("grafana/dashboards").glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8-sig"))
        uid = dashboard.get("uid", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", uid) or uid in dashboards:
            raise SystemExit(f"Invalid or duplicate dashboard UID in {path}")
        panels = dashboard.get("panels", [])
        panel_ids = [panel["id"] for panel in panels]
        if not panel_ids or len(set(panel_ids)) != len(panel_ids):
            raise SystemExit(f"Empty dashboard or duplicate panel IDs in {path}")
        dashboards[uid] = dashboard
    if not {"codex-overview", "codex-windows", "codex-usage", "codex-requests", "codex-network", "codex-evaluation"} <= dashboards.keys():
        raise SystemExit("Expected Grafana dashboard files are missing")
    return dashboards


def queries(panels):
    for panel in panels:
        yield from queries(panel.get("panels", []))
        for target in panel.get("targets", []):
            if target.get("expr") and panel.get("datasource", {}).get("uid") == "prometheus":
                yield panel["title"], target["expr"]


def verify_live(dashboards, provider_fixture=False):
    runtime = Path(os.environ["CODEX_OBSERVABILITY_RUNTIME_DIR"])
    password = (runtime / "grafana-admin-password.txt").read_text(encoding="utf-8").strip()
    authorization = base64.b64encode(f"admin:{password}".encode()).decode()

    def get(path):
        request = Request(f"{GRAFANA}{path}", headers={"Authorization": f"Basic {authorization}"})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    data_sources = get("/api/datasources")
    if not {"prometheus", "tempo", "loki"} <= {source["uid"] for source in data_sources}:
        raise SystemExit("One or more expected Grafana data sources are missing")

    count = 0
    for uid in dashboards:
        dashboard = get(f"/api/dashboards/uid/{uid}")["dashboard"]
        for title, query in queries(dashboard["panels"]):
            for variable, value in TEMPLATE_VALUES.items():
                query = query.replace(variable, value)
            if re.search(r"\$[A-Za-z_{]", query):
                raise SystemExit(f"Unknown Grafana template in {uid} / {title}")
            endpoint = "/api/datasources/proxy/uid/prometheus/api/v1/query"
            try:
                result = get(f"{endpoint}?{urlencode({'query': query})}")
            except OSError as error:
                raise SystemExit(f"Prometheus query failed in {uid} / {title}: {type(error).__name__}") from None
            if result.get("status") != "success":
                raise SystemExit(f"Prometheus query failed in {uid} / {title}")
            count += 1
    if count == 0:
        raise SystemExit("No provisioned Prometheus expressions were validated")
    print(f"Evaluated {count} provisioned Grafana Prometheus expressions.")
    if provider_fixture:
        verify_provider_fixture(get, dashboards["codex-requests"])


def verify_provider_fixture(get, dashboard):
    probe = runpy.run_path("scripts/provider_probe.py")
    end = time.time_ns()
    start = end - 100_000_000
    trace_id, probe_trace_id, span_id = uuid4().hex, uuid4().hex, uuid4().hex[:16]
    request_id = "req_synthetic_" + uuid4().hex
    stripped = "synthetic-body-" + uuid4().hex

    def string(key, value):
        return {"key": key, "value": {"stringValue": value}}

    def integer(key, value):
        return {"key": key, "value": {"intValue": str(value)}}

    resource = {"attributes": [string("service.name", "codex-provider-test")]}
    scope = {"name": "codex-observability-provider-test"}
    signals = probe["payloads"](probe["measurement"]("401,1,0.01,0.04,0.09,0.18,0.19", 0), start, end, probe_trace_id)
    signals["logs"] = {"resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": scope, "logRecords": [{
        "timeUnixNano": str(end), "traceId": trace_id, "spanId": span_id,
        "body": {"stringValue": stripped}, "attributes": [
            string("event.name", "codex.api_request"), string("model", "synthetic"), integer("attempt", 1),
            string("auth.request_id", request_id), string("arguments", stripped),
        ],
    }]}]}]}
    signals["traces"]["resourceSpans"].append({"resource": resource, "scopeSpans": [{"scope": scope, "spans": [
        {"traceId": trace_id, "spanId": span_id, "name": "responses.stream_request", "kind": 3,
         "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
         "attributes": [string("auth.request_id", request_id), string("url.full", stripped), string("transport", "responses_http")]},
        {"traceId": trace_id, "spanId": uuid4().hex[:16], "parentSpanId": span_id, "name": "completed", "kind": 1,
         "startTimeUnixNano": str(end), "endTimeUnixNano": str(end + 1),
         "attributes": [integer("gen_ai.usage.input_tokens", 42), integer("gen_ai.usage.output_tokens", 11)]},
    ]}]})
    probe["emit"](signals)

    loki = "/api/datasources/proxy/uid/loki/loki/api/v1"
    tempo = "/api/datasources/proxy/uid/tempo/api"
    prom = "/api/datasources/proxy/uid/prometheus/api/v1/query"
    retry_expr = next(target["expr"] for panel in dashboard["panels"] if panel.get("datasource", {}).get("uid") == "loki" for target in panel["targets"])
    retry_expr = retry_expr.replace("$model", "synthetic").replace("$__interval", "2m")
    trace_exprs = [target["query"] for panel in dashboard["panels"] if panel.get("datasource", {}).get("uid") == "tempo" for target in panel["targets"]]
    if len(trace_exprs) < 2:
        raise SystemExit("Expected request and completion TraceQL panels")

    def query(path, **params):
        return get(f"{path}?{urlencode(params)}")

    checks = {
        "retry LogQL": lambda: bool(query(f"{loki}/query", query=retry_expr)["data"]["result"]),
        "retained and filtered log metadata": lambda: check_metadata(query(
            f"{loki}/query_range", query='{service_name="codex-provider-test"}', start=start,
        ), request_id, stripped),
        "retained and filtered span metadata": lambda: check_metadata(get(f"{tempo}/traces/{trace_id}"), request_id, stripped),
        "provider probe gauge": lambda: bool(query(prom, query='codex_provider_probe_duration_seconds{phase="tls_handshake"} > 0')["data"]["result"]),
        "provider probe span": lambda: check_metadata(get(f"{tempo}/traces/{probe_trace_id}"), "network.probe.tls_handshake_ms", stripped),
    }
    for index, expression in enumerate(trace_exprs, 1):
        def contains_trace(expression=expression):
            result = query(f"{tempo}/search", q=expression, start=start // 1_000_000_000 - 30, end=end // 1_000_000_000 + 1, limit=50)
            return any(item.get("traceID") == trace_id for item in result.get("traces", []))
        checks[f"request TraceQL {index}"] = contains_trace

    deadline = time.monotonic() + 40
    while checks and time.monotonic() < deadline:
        for name, check in list(checks.items()):
            try:
                if check():
                    del checks[name]
            except OSError:
                continue
        if checks:
            time.sleep(1)
    if checks:
        raise SystemExit("Synthetic provider telemetry did not pass: " + ", ".join(checks))
    print("Validated synthetic request retries, request and completion traces, redaction and network probe.")


def check_metadata(doc, expected, stripped):
    result = json.dumps(doc)
    if stripped in result:
        raise SystemExit("Private fixture marker was retained by the Collector")
    return expected in result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider-fixture", action="store_true", help="Use only on an isolated synthetic stack")
    args = parser.parse_args()
    files = read_dashboards()
    print(f"Validated {len(files)} source dashboards.")
    if args.live:
        verify_live(files, args.provider_fixture)
