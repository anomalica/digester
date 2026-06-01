#!/usr/bin/env python3
"""Wrapper that runs a digester or assembler command and commits the
resulting output-repo changes with a structured message capturing the
pipeline version.

This is the interim mechanism per anomalica/decisions/0028 follow-up:
every regeneration produces an audit trail so we can `git log` to see
every page produced under a given pipeline version, and diff against
baselines (e.g. `baseline-2026-05-24`) to see how a page has changed.

The wrapper watches two output repos:
  - content (assembler output: pages/)
  - digests (digester output: records/, store/)

It snapshots `git status --porcelain` in both repos before running the
command, then commits the new diff in any repo that changed, with a
structured message of this shape:

  regen: <inferred-target> via <stage>

  stage: digester-extract | digester-import | assembler-regen | other
  assembler-sha: <git sha of assembler HEAD>
  digester-sha: <git sha of digester HEAD>
  db-version: <contents of workspace/.db-version>
  files-touched: <count>

Usage:
  regen-commit.py --stage assembler-regen -- python assembler.py --node "Foo"
  regen-commit.py --stage digester-extract -- python -m digester extract X.md

Pass --skip-commit to dry-run (still runs the command, just doesn't commit).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPOS = {
    "content": Path("/home/mark/repos/anomalica/content"),
    "digests": Path("/home/mark/repos/anomalica/digests"),
}

ASSEMBLER_REPO = Path("/home/mark/repos/anomalica/assembler")
DIGESTER_REPO = Path("/home/mark/repos/anomalica/digester")
DB_VERSION_FILE = DIGESTER_REPO / "workspace" / ".db-version"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )
    return res.stdout.strip()


def repo_status(repo: Path) -> set[str]:
    """Set of `XY path` lines from porcelain status. Includes untracked.

    Calls git directly here (not via _git) because _git strips whitespace
    from the full output, which would clip the leading space off the first
    porcelain line (` M path` -> `M path`) and break porcelain_to_path.
    """
    res = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(line for line in res.stdout.splitlines() if line.strip())


def repo_sha(repo: Path) -> str:
    try:
        return _git(repo, "rev-parse", "--short=12", "HEAD")
    except subprocess.CalledProcessError:
        return "(no-head)"


def db_version() -> str:
    try:
        return DB_VERSION_FILE.read_text().strip()
    except FileNotFoundError:
        return "(unset)"


def infer_target(stage: str, cmd: list[str]) -> str:
    """Best-effort target name from the command line, for the commit subject."""
    joined = " ".join(cmd)
    for flag in ("--node", "--record", "--source", "--input"):
        if flag in cmd:
            i = cmd.index(flag)
            if i + 1 < len(cmd):
                return cmd[i + 1]
    if stage.startswith("assembler"):
        return joined[:60]
    if stage.startswith("digester"):
        return joined[:60]
    return joined[:60]


def commit_changes(
    repo: Path,
    stage: str,
    target: str,
    note: str | None,
    new_paths: list[str],
) -> str | None:
    """Stage new_paths in repo and create one commit. Returns the new SHA,
    or None if nothing was committed (e.g. no actual diff after staging).
    """
    if not new_paths:
        return None
    # Stage exactly the changed paths - never `git add .`, never include
    # other in-flight work. new_paths comes from the porcelain diff so it
    # already excludes anything that was dirty before the command ran.
    for p in new_paths:
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", p],
            check=True,
        )

    staged = _git(repo, "diff", "--cached", "--name-only")
    if not staged:
        return None

    subject = f"regen: {target} via {stage}"
    body_lines = [
        f"stage: {stage}",
        f"assembler-sha: {repo_sha(ASSEMBLER_REPO)}",
        f"digester-sha: {repo_sha(DIGESTER_REPO)}",
        f"db-version: {db_version()}",
        f"files-touched: {len(staged.splitlines())}",
    ]
    if note:
        body_lines.append(f"note: {note}")
    msg = subject + "\n\n" + "\n".join(body_lines) + "\n"

    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", msg],
        check=True,
    )
    return _git(repo, "rev-parse", "--short=12", "HEAD")


def porcelain_to_path(line: str) -> str:
    """Extract the path from a porcelain status line (handles renames)."""
    # Lines are 'XY path' or 'XY oldpath -> newpath' for renames.
    body = line[3:]
    if " -> " in body:
        return body.split(" -> ", 1)[1]
    return body


def _check_repos_clean_at_start(repos: dict[str, Path]) -> dict[str, set[str]]:
    """Snapshot pre-state. We commit what is NEW after the run, so any pre-
    existing dirty state in these repos is preserved untouched."""
    return {name: repo_status(path) for name, path in repos.items()}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--stage",
        required=True,
        choices=[
            "assembler-regen",
            "digester-extract",
            "digester-import",
            "digester-migrate",
            "other",
        ],
        help="Pipeline stage producing the output. Goes in the commit message.",
    )
    ap.add_argument(
        "--note",
        help="Optional free-text note appended to the commit message body.",
    )
    ap.add_argument(
        "--skip-commit",
        action="store_true",
        help="Run the command, report what would be committed, but do not commit.",
    )
    ap.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="The command to run after '--'.",
    )
    args = ap.parse_args()

    cmd = args.cmd
    # argparse leaves a leading "--" in cmd; strip it.
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("no command supplied (use '-- <command>' after the flags)")

    pre = _check_repos_clean_at_start(REPOS)

    print(f"[regen-commit] stage={args.stage}", file=sys.stderr)
    print(f"[regen-commit] running: {' '.join(cmd)}", file=sys.stderr)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(
            f"[regen-commit] command exited {rc}; skipping commits",
            file=sys.stderr,
        )
        return rc

    target = infer_target(args.stage, cmd)
    any_committed = False
    for name, repo in REPOS.items():
        post = repo_status(repo)
        new_lines = sorted(post - pre[name])
        if not new_lines:
            continue
        new_paths = [porcelain_to_path(line) for line in new_lines]
        print(
            f"[regen-commit] {name}: {len(new_paths)} new diff(s)",
            file=sys.stderr,
        )
        for p in new_paths:
            print(f"  {p}", file=sys.stderr)
        if args.skip_commit:
            print(
                f"[regen-commit] {name}: --skip-commit, not committing", file=sys.stderr
            )
            continue
        sha = commit_changes(repo, args.stage, target, args.note, new_paths)
        if sha:
            any_committed = True
            print(f"[regen-commit] {name}: committed as {sha}", file=sys.stderr)

    if not any_committed and not args.skip_commit:
        print(
            "[regen-commit] no output changes detected; nothing committed",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
