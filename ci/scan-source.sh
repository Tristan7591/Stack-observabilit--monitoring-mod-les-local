#!/usr/bin/env bash
set -euo pipefail

source_dir="$(mktemp -d "${RUNNER_TEMP:?}/source-scan.XXXXXX")"
report="$(mktemp "$RUNNER_TEMP/source-report.XXXXXX")"
git archive --format=tar HEAD | tar -xf - -C "$source_dir"
trivy fs --quiet --scanners secret,misconfig --skip-check-update --format json --output "$report" "$source_dir"
secret_count="$(jq '[.Results[]?.Secrets[]?] | length' "$report")"
config_count="$(jq '[.Results[]?.Misconfigurations[]? | select(.Status == "FAIL")] | length' "$report")"
blocked_config_count="$(jq '[.Results[]?.Misconfigurations[]? | select(.Status == "FAIL" and (.Severity == "CRITICAL" or .Severity == "HIGH"))] | length' "$report")"
printf 'Static scan found %s secret finding(s) and %s configuration finding(s).\n' "$secret_count" "$config_count"
# Print configuration rule IDs only, and suppress secret fields entirely.
jq -r '[.Results[]?.Misconfigurations[]? | select(.Status == "FAIL") | [.ID, .Severity]] | unique[] | @tsv' "$report"
rm -- "$report"
test "$secret_count" -eq 0 && test "$blocked_config_count" -eq 0
