"""Inspect staged/tracked release files and commit identities before publication."""
from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath

ALLOWED_DATA = {"data/input/seed_companies.csv"}
ALLOWED_PEOPLE = {"MahmoudAsadi97", "Mahmoud Asadi Heris"}
ALLOWED_EMAILS = {"Asadiherism@gmail.com"}
PATTERNS = [rb"gh[pousr]_[A-Za-z0-9]{30,}", rb"github_pat_[A-Za-z0-9_]{40,}",
            rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def main() -> int:
    problems = []
    paths = git("ls-files", "--cached", "-z").decode().split("\0")
    checked = 0
    for path in filter(None, paths):
        data = git("show", f":{path}")
        checked += 1
        p = PurePosixPath(path)
        if any(part in {".venv", "__pycache__", "node_modules", "build", "dist", ".pytest_cache"} for part in p.parts):
            problems.append(f"Generated directory: {path}")
        if path.startswith(("data/cache/", "data/workspace/")) or (path.startswith("data/output/") and p.name != ".gitkeep"):
            problems.append(f"Runtime data: {path}")
        if p.name.startswith(".env") and p.name != ".env.example":
            problems.append(f"Local environment: {path}")
        if p.suffix in {".sqlite", ".sqlite3", ".db", ".xlsx", ".xls", ".xlsm", ".zip", ".pem", ".key"}:
            problems.append(f"Non-source artifact: {path}")
        if p.suffix in {".csv", ".tsv"} and path not in ALLOWED_DATA and not path.startswith(("tests/fixtures/", "src/dataharvest/examples/")):
            problems.append(f"Unexpected data input: {path}")
        if len(data) > 2 * 1024 * 1024:
            problems.append(f"File exceeds 2 MiB release budget: {path}")
        if any(re.search(pattern, data) for pattern in PATTERNS):
            problems.append(f"Credential-like content: {path}")
    for line in git("log", "--all", "--format=%an|%ae|%cn|%ce").decode().splitlines():
        author, email, committer, commit_email = line.split("|")
        if author not in ALLOWED_PEOPLE or email not in ALLOWED_EMAILS:
            problems.append("Unexpected commit author identity")
        if (committer not in ALLOWED_PEOPLE or commit_email not in ALLOWED_EMAILS) and (committer, commit_email) != ("GitHub", "noreply@github.com"):
            problems.append("Unexpected commit committer identity")
    if problems:
        print("\n".join(sorted(set(problems))))
        return 1
    print(f"Release file check passed: {checked} staged/tracked files; expected commit identities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
