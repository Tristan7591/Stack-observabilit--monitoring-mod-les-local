"""Check the resolved Compose model against the test-runner privacy boundary."""

import json
import os
from pathlib import Path
import re
import sys


ROOT = Path.cwd().resolve()
RUNTIME = Path(os.environ["CODEX_OBSERVABILITY_RUNTIME_DIR"]).resolve()
IMAGE_REF = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


def require(condition, reason):
    if not condition:
        raise SystemExit(reason)


model = json.load(sys.stdin)
for name, service in model["services"].items():
    require(IMAGE_REF.fullmatch(service.get("image", "")), f"Unpinned image for {name}")
    require(not service.get("build"), f"Unexpected build context for {name}")
    for field in ("privileged", "devices", "cap_add"):
        require(not service.get(field), f"Unexpected {field} for {name}")
    for field in ("network_mode", "pid", "ipc", "userns_mode"):
        require(service.get(field) != "host", f"Host {field} for {name}")
    for port in service.get("ports", []):
        require(port.get("host_ip") in {"127.0.0.1", "::1"}, f"Published non-loopback port for {name}")
    for volume in service.get("volumes", []):
        if volume["type"] == "bind":
            source = Path(volume["source"]).resolve()
            require(volume.get("read_only"), f"Writable bind mount for {name}")
            require(source.is_relative_to(ROOT) or source == RUNTIME / "metrics", f"Unapproved bind mount for {name}")
            require(not source.name.endswith(".sock"), f"Socket bind mount for {name}")

require(set(model.get("secrets", {})) == {"grafana_admin_password"}, "Unexpected Compose secret")
require(
    Path(model["secrets"]["grafana_admin_password"]["file"]).resolve() == RUNTIME / "grafana-admin-password.txt",
    "Grafana credential does not point at the synthetic runtime",
)
print("Compose image pins, mounts and network boundary validated.")
