#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${CODEX_OBSERVABILITY_RUNTIME_DIR:?}"
install -d -m 0700 "$runtime_dir"
install -d -m 0755 "$runtime_dir/metrics"
if [[ ! -f "$runtime_dir/grafana-admin-password.txt" ]]; then
  openssl rand -hex 32 > "$runtime_dir/grafana-admin-password.txt"
  # Docker mounts the file into Grafana's unprivileged runtime account.
  chmod 0444 "$runtime_dir/grafana-admin-password.txt"
fi

write_host_metrics() {
  printf '%s\n' \
    '# TYPE codex_host_collector_last_success_timestamp_seconds gauge' \
    "codex_host_collector_last_success_timestamp_seconds $(date +%s)" \
    '# TYPE codex_host_process_count gauge' \
    'codex_host_process_count 1' > "$runtime_dir/metrics/host.tmp"
  chmod 0644 "$runtime_dir/metrics/host.tmp"
  mv -f -- "$runtime_dir/metrics/host.tmp" "$runtime_dir/metrics/host.prom"
}

write_host_metrics
