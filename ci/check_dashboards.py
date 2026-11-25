"""Validate dashboard structure and exercise provisioned Prometheus queries in CI."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
    if not {"codex-overview", "codex-windows", "codex-usage"} <= dashboards.keys():
        raise SystemExit("Expected Grafana dashboard files are missing")
    return dashboards


def queries(panels):
    for panel in panels:
        yield from queries(panel.get("panels", []))
        for target in panel.get("targets", []):
            if target.get("expr") and panel.get("datasource", {}).get("uid") == "prometheus":
                yield panel["title"], target["expr"]


def verify_live(dashboards):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    files = read_dashboards()
    print(f"Validated {len(files)} source dashboards.")
    if args.live:
        verify_live(files)
