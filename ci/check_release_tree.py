"""Reject unexpected staged paths and obvious workstation data before publishing."""

import argparse
import re
import subprocess
import sys


ALLOWED_PATH = re.compile(
    r"(?:README\.md|CHANGELOG\.md|LICENSE(?:\.md)?|SECURITY\.md|compose\.ya?ml|"
    r"\.gitignore|\.dockerignore|\.gitattributes|[^/]+\.ps1|"
    r"\.githooks/pre-push|\.github/(?:dependabot\.ya?ml|workflows/[^/]+\.ya?ml)|docs/[^/]+\.md|"
    r"config/[^/]+\.ya?ml|grafana/dashboards/[^/]+\.json|"
    r"grafana/provisioning/(?:dashboards|datasources)/[^/]+\.ya?ml|"
    r"scripts/[^/]+\.(?:ps1|py)|ci/[^/]+\.(?:py|sh|ps1|ya?ml)|"
    r"fixtures/(?:Dockerfile|\.dockerignore|[^/]+\.py))"
)
SENSITIVE_NAME = re.compile(
    r"(?:^|[./_-])(?:auth|credential|password|session[_-]index|secret|rollout|transcript)"
    r"(?:[./_-]|$)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = (
    ("absolute workstation path", re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][A-Za-z0-9_-]+|/(?:home|Users)/[A-Za-z0-9_-]+/")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("provider credential", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}|AKIA[A-Z0-9]{16})\b")),
)


def git(*args):
    return subprocess.check_output(("git", *args))


def tracked_files(revision):
    entries = git("ls-tree", "-r", "-z", revision) if revision else git("ls-files", "--stage", "-z")
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, filename = entry.decode("utf-8", errors="replace").split("\t", 1)
        if revision:
            mode, kind, blob = metadata.split()
            valid = kind == "blob"
        else:
            mode, blob, stage = metadata.split()
            valid = stage == "0"
        yield mode, blob, valid, filename


def check_tree(revision, checked):
    issues = 0
    for mode, blob, valid, filename in tracked_files(revision):
        if (
            not valid
            or mode not in {"100644", "100755"}
            or not ALLOWED_PATH.fullmatch(filename)
            or SENSITIVE_NAME.search(filename)
        ):
            print("Rejected unexpected tracked path or file type; path omitted")
            issues += 1
            continue
        if (filename, blob) in checked:
            continue
        checked.add((filename, blob))

        try:
            content = git("cat-file", "blob", blob).decode("utf-8-sig")
        except UnicodeDecodeError:
            print(f"Rejected non-text file: {filename!r}")
            issues += 1
            continue
        if "\0" in content:
            print(f"Rejected non-text file: {filename!r}")
            issues += 1
        for description, pattern in SENSITIVE_TEXT:
            # Rules are definitions here. Trivy still scans this file.
            if filename != "ci/check_release_tree.py" and pattern.search(content):
                print(f"Found {description} in {filename!r}; value omitted")
                issues += 1

    return issues


def outgoing_commits(lines):
    commits = set()
    for line in lines:
        _, local, _, remote = line.split()
        if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) for sha in (local, remote)):
            raise SystemExit("Invalid ref update received from Git.")
        if not int(local, 16):
            continue
        revisions = [local]
        if int(remote, 16):
            available = subprocess.run(("git", "cat-file", "-e", f"{remote}^{{commit}}"), capture_output=True)
            if available.returncode == 0:
                revisions.append(f"^{remote}")
        commits.update(git("rev-list", *revisions).decode("ascii").splitlines())
    return sorted(commits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-push", action="store_true", help="Check outgoing commit trees from Git's hook input")
    args = parser.parse_args()
    revisions = outgoing_commits(sys.stdin) if args.pre_push else [None]
    checked = set()
    issues = sum(check_tree(revision, checked) for revision in revisions)
    if issues:
        raise SystemExit(f"Release check found {issues} privacy or source policy issue(s).")
    if args.pre_push:
        print(f"Privacy check passed for {len(revisions)} outgoing commit(s).")
    else:
        print("Release tree privacy check passed.")


if __name__ == "__main__":
    main()
