#!/usr/bin/env bash
set -euo pipefail

fixture="${FIXTURE_IMAGE:?}"
cache_dir="${RUNNER_TEMP:?}/trivy-cache"
report="$(mktemp "$RUNNER_TEMP/image-report.XXXXXX")"
trap 'rm -f -- "$report"' EXIT
refs="$(docker compose --profile advisor config --images)"
mapfile -t images < <(printf '%s\n%s\n' "$refs" "$fixture" | sort -u)
trivy image --cache-dir "$cache_dir" --quiet --download-db-only

blocked=0
for image in "${images[@]}"; do
  printf '\nScanning image %s\n' "$image"
  trivy image --cache-dir "$cache_dir" --quiet --skip-db-update --image-src docker --offline-scan \
    --scanners vuln --severity HIGH,CRITICAL --format json --output "$report" "$image"
  jq -r '[.Results[]?.Vulnerabilities[]? | [.Severity, .VulnerabilityID, .PkgName, .InstalledVersion, (.FixedVersion // "")]] | unique[] | @tsv' "$report"
  fixed_count="$(jq '[.Results[]?.Vulnerabilities[]? | select((.FixedVersion // "") != "")] | length' "$report")"
  total_count="$(jq '[.Results[]?.Vulnerabilities[]?] | length' "$report")"
  printf 'HIGH/CRITICAL findings with a vendor fix: %s. Total HIGH/CRITICAL findings: %s.\n' "$fixed_count" "$total_count"
  if (( fixed_count > 0 )); then
    blocked=1
  fi
done

exit "$blocked"
