"""Review-gate: decide whether a record may be digested.

Quality gate, not a cost gate: the knowledge graph must not be built from
unreviewed material. The digestibility RULE (the threshold, the content-unit
definition, the sidecar reading) is single-sourced in anomalica_common.review_gate
so the digester and the workbench compute the same verdict. This module is the
digester's file/sidecar wrappers around that core: locating a record's
review-coverage sidecar and the path-resolving entry point used by the `coverage`
CLI command.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import yaml
from anomalica_common.review_gate import (
    CarryoverState,
    Digestibility,
    ReviewBindingState,
    digestibility,
    parsed_record_body,
)

__all__ = [
    "Digestibility",
    "digestibility",
    "load_sidecar",
    "assess_record",
    "review_provenance",
]

# A sidecar written by something automated must SAY SO. `by` is a free-text
# address with no machine/human discriminator, so "reviewed" stops meaning "a
# human looked" the moment anything automated learns to write there - and an AI
# triage pass is coming. The burden therefore sits on the new writer to identify
# itself rather than on every reader to guess: a positive marker wins, and only
# in its absence does a sidecar imply a human.
_MACHINE_MARKERS = ("anomalica/triage/", "triage_model", "produced_by_model")


def _carryover_of(record_md: Path) -> dict | None:
    """A predecessor record's review, carried forward when this one superseded it.

    A record can show `state: none` - no sidecar of its own - while a HUMAN
    reviewed the version it replaced and made text edits to it. Read without this,
    the graph says nobody has ever looked at that material, which is absence being
    read as a value. Surfaced ALONGSIDE state rather than folded into it: "was this
    record reviewed" is still `state == "human"`, and "has this material had human
    attention" is a second, weaker question that now has an answer.
    """
    import yaml as _yaml

    try:
        raw = record_md.resolve().read_text(errors="replace")
    except OSError:
        return None
    if not raw.startswith("---"):
        return None
    try:
        fm = _yaml.safe_load(raw.split("---", 2)[1])
    except _yaml.YAMLError:
        return None
    co = (fm or {}).get("review_carryover") if isinstance(fm, dict) else None
    return co if isinstance(co, dict) else None


def review_provenance(record_md: Path, ingests_dir: Path) -> dict:
    """What is known about who observed this record, for stamping into a digest.

    Review has never been enforced in the extract path, so a digest from
    unreviewed OCR is otherwise indistinguishable from one a reviewer read line
    by line. This records the difference rather than deciding on it.

    `state` is the field a consumer filters on and is machine-derivable:
    ``machine`` when the sidecar identifies an automated writer, ``human`` when a
    sidecar exists without one, ``none`` when there is no sidecar at all.
    """
    carryover = _carryover_of(record_md)
    sidecar = load_sidecar(record_md, ingests_dir)
    if sidecar is None:
        out = {"state": "none", "sidecar": "absent"}
        if carryover:
            out["carryover"] = carryover
        return out
    blob = json.dumps(sidecar).lower()
    state = "machine" if any(m in blob for m in _MACHINE_MARKERS) else "human"
    cov = sidecar.get("observed_coverage")
    out = {"state": state, "sidecar": "present"}
    if carryover:
        out["carryover"] = carryover
    if cov is not None:
        out["observed_coverage"] = round(float(cov), 4)
    return out


# A record body is {hash}.md or {hash}.v2.md (a processing-version infix), but
# its review sidecar is keyed by the BARE content hash: {hash}.review.json. Strip
# any version infix when deriving the sidecar name, else v2 records never find
# their sidecar and read as unreviewed.
_BODY_SUFFIX = re.compile(r"(\.v\d+)?\.md$")
_RECORD_NAME = re.compile(r"(?P<hash>[0-9a-f]{64})(?:\.v\d+)?\.md$")


def _sidecar_path(record_md: Path) -> Path | None:
    target = record_md.resolve()
    fname = _BODY_SUFFIX.sub(".review.json", target.name)
    return next(
        (
            directory / fname
            for directory in (target.parent, target.parent.parent)
            if (directory / fname).is_file()
        ),
        None,
    )


def load_sidecar(record_md: Path, ingests_dir: Path) -> dict | None:
    """Find a record's review-coverage sidecar by its store hash.

    The .md may live under store/v1/ while the sidecar sits at the store root
    (the v1/ reorg moved record bodies but not sidecars), so search the
    colocated directory and walk up to the parent store dir.
    """
    sidecar = _sidecar_path(record_md)
    if sidecar is None:
        return None
    try:
        document = json.loads(sidecar.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _repo_root(path: Path) -> Path | None:
    raw = _git(path, "rev-parse", "--show-toplevel")
    if raw is None:
        return None
    try:
        return Path(raw.decode("utf-8").strip()).resolve()
    except UnicodeDecodeError:
        return None


def _git_blob(repo: Path, revision: str, relative_path: str) -> str | None:
    raw = _git(repo, "show", f"{revision}:{relative_path}")
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _record_content_hash(record_text: str) -> str | None:
    match = re.match(
        r"\A---(?:\r\n|\r|\n)(.*?)(?:\r\n|\r|\n)---(?:\r\n|\r|\n|\Z)",
        record_text,
        re.DOTALL,
    )
    if match is None:
        return None
    try:
        frontmatter = yaml.load(match.group(1), Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return None
    value = frontmatter.get("content_hash") if isinstance(frontmatter, dict) else None
    return value if isinstance(value, str) else None


def _record_at_commit(
    repo: Path, revision: str, record_md: Path, record_hash: str
) -> str | None:
    try:
        current_relative = str(record_md.resolve().relative_to(repo))
    except ValueError:
        return None
    current = _git_blob(repo, revision, current_relative)
    if current is not None and _record_content_hash(current) == record_hash:
        return current

    tree = _git(repo, "ls-tree", "-r", "--name-only", "-z", revision, "--", "store")
    if tree is None:
        return None
    try:
        paths = [item.decode("utf-8") for item in tree.split(b"\0") if item]
    except UnicodeDecodeError:
        return None
    bare_hash = record_hash.removeprefix("sha256:")
    candidates = [
        path
        for path in paths
        if (name := _RECORD_NAME.fullmatch(Path(path).name))
        and name.group("hash") == bare_hash
    ]
    records = [
        record
        for path in candidates
        if (record := _git_blob(repo, revision, path)) is not None
        and _record_content_hash(record) == record_hash
    ]
    if not records or any(
        parsed_record_body(item) != parsed_record_body(records[0])
        for item in records[1:]
    ):
        return None
    return records[0]


def _legacy_body_validated(
    record_md: Path, sidecar_path: Path, sidecar: dict, record_text: str
) -> bool:
    repo = _repo_root(sidecar_path.parent)
    if repo is None:
        return False
    try:
        sidecar_relative = str(sidecar_path.resolve().relative_to(repo))
    except ValueError:
        return False

    status = _git(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        sidecar_relative,
    )
    if status is None or status:
        return False
    latest_raw = _git(repo, "log", "-1", "--format=%H", "HEAD", "--", sidecar_relative)
    if latest_raw is None:
        return False
    try:
        latest = latest_raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", latest):
        return False

    reviews = sidecar.get("reviews")
    latest_review = reviews[-1] if isinstance(reviews, list) and reviews else None
    parent_commit = (
        latest_review.get("parent_commit") if isinstance(latest_review, dict) else None
    )
    parents_raw = _git(repo, "rev-list", "--parents", "-n", "1", latest)
    if parents_raw is None:
        return False
    try:
        commit_and_parents = parents_raw.decode("ascii").split()
    except UnicodeDecodeError:
        return False
    parents = commit_and_parents[1:]
    if not isinstance(parent_commit, str) or parent_commit not in parents:
        return False

    # A committed delete followed by a fresh add is not continuous review evidence.
    if not any(
        _git_blob(repo, parent, sidecar_relative) is not None for parent in parents
    ):
        for parent in parents:
            prior = _git(
                repo, "log", "-1", "--format=%H", parent, "--", sidecar_relative
            )
            if prior is None or prior.strip():
                return False

    record_hash = _record_content_hash(record_text)
    if record_hash is None:
        return False
    reviewed_record = _record_at_commit(repo, latest, record_md, record_hash)
    return reviewed_record is not None and parsed_record_body(
        reviewed_record
    ) == parsed_record_body(record_text)


def _carryover_state(record_text: str, sidecar: dict) -> CarryoverState:
    match = re.match(
        r"\A---(?:\r\n|\r|\n)(.*?)(?:\r\n|\r|\n)---(?:\r\n|\r|\n|\Z)",
        record_text,
        re.DOTALL,
    )
    if match is None:
        return CarryoverState.ABSENT
    try:
        frontmatter = yaml.load(match.group(1), Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return CarryoverState.UNRESOLVED
    carryover = (
        frontmatter.get("review_carryover") if isinstance(frontmatter, dict) else None
    )
    if carryover is None:
        return CarryoverState.ABSENT
    if not isinstance(carryover, dict) or type(carryover.get("at")) is not str:
        return CarryoverState.UNRESOLVED
    try:
        marker = datetime.fromisoformat(carryover["at"].replace("Z", "+00:00"))
    except ValueError:
        return CarryoverState.UNRESOLVED
    if marker.tzinfo is None:
        return CarryoverState.UNRESOLVED
    reviews = sidecar.get("reviews")
    if not isinstance(reviews, list):
        return CarryoverState.UNRESOLVED
    for review in reviews:
        reviewed_at = review.get("at") if isinstance(review, dict) else None
        if type(reviewed_at) is not str:
            continue
        try:
            timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is not None and timestamp >= marker:
            return CarryoverState.RESOLVED
    return CarryoverState.UNRESOLVED


def assess_record(
    record_md: Path, ingests_dir: Path, threshold: float = 1.0
) -> Digestibility:
    text = record_md.resolve().read_bytes().decode("utf-8")
    sidecar_path = _sidecar_path(record_md)
    sidecar = load_sidecar(record_md, ingests_dir)
    if sidecar is None or sidecar_path is None:
        return digestibility(text, None, threshold)
    binding = ReviewBindingState(
        legacy_body_validated=(
            "reviewed_body_sha256" not in sidecar
            and _legacy_body_validated(record_md, sidecar_path, sidecar, text)
        ),
        carryover=_carryover_state(text, sidecar),
    )
    return digestibility(text, sidecar, threshold, binding=binding)
