#!/usr/bin/env bash
set -euo pipefail

tools_dir="${RUNNER_TEMP:?}/codex-ci-tools"
mkdir -p "$tools_dir"

install_release() {
  local binary="$1" url="$2" digest="$3" archive="$tools_dir/$1.tar.gz"
  if [[ -x "$tools_dir/$binary" ]]; then
    return
  fi
  curl --fail --silent --show-error --location --retry 3 --proto '=https' --proto-redir '=https' "$url" --output "$archive"
  printf '%s  %s\n' "$digest" "$archive" | sha256sum --check --status
  tar --extract --gzip --file "$archive" --directory "$tools_dir" "$binary"
  rm -- "$archive"
}

for requested in "$@"; do
  case "$requested" in
    actionlint)
      install_release actionlint 'https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz' \
        '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8'
      ;;
    trivy)
      install_release trivy 'https://github.com/aquasecurity/trivy/releases/download/v0.76.2/trivy_0.76.2_Linux-64bit.tar.gz' \
        'bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf'
      ;;
    *) printf 'Unknown CI tool: %s\n' "$requested" >&2; exit 2 ;;
  esac
done

printf '%s\n' "$tools_dir" >> "${GITHUB_PATH:?}"
