"""Deterministic account extraction scorer with inspectable match evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


MATCH_THRESHOLD = 0.28
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "near",
    "of",
    "on",
    "the",
    "to",
    "two",
    "who",
    "with",
}


def _tokens(value: str | None) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (value or "").lower().replace("'s", ""))
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in words
        if word not in _STOP_WORDS
    }


def _overlap(left: str | None, right: str | None) -> tuple[float, list[str]]:
    a, b = _tokens(left), _tokens(right)
    shared = sorted(a & b)
    return (2 * len(shared) / (len(a) + len(b)) if a and b else 0.0), shared


def _line_range(account: dict) -> tuple[int, int] | None:
    start = account.get("approx_line_start", account.get("line_start"))
    end = account.get("approx_line_end", account.get("line_end"))
    if isinstance(start, int) and isinstance(end, int) and start <= end:
        return start, end
    return None


def _phrase_offset(body: str, phrase: str) -> int | None:
    phrase = phrase.strip()
    if not phrase:
        return None
    pattern = r"\s+".join(re.escape(token) for token in phrase.split())
    match = re.search(pattern, body, re.IGNORECASE)
    return match.start() if match else None


def _span_line_range(account: dict, body: str) -> tuple[int, int] | None:
    span = account.get("span") or ""
    candidates = []
    for separator in (match.start() for match in re.finditer("-", span)):
        start_phrase, end_phrase = span[:separator], span[separator + 1 :]
        start = _phrase_offset(body, start_phrase)
        end = _phrase_offset(body, end_phrase)
        if start is not None and end is not None and end >= start:
            candidates.append((end - start, start, end + len(end_phrase.strip())))
    if not candidates:
        return None
    _, start, end = max(candidates)

    # The hand-labelled Doty boundaries count substantive pre-digest lines and
    # omit blank separators. Preserve speaker comments because the gold did.
    def line_number(offset):
        completed_lines = body[:offset].split("\n")[:-1]
        return sum(bool(line.strip()) for line in completed_lines) + 1

    return line_number(start), line_number(end)


def _boundary_evidence(
    gold: dict, predicted: dict, source_body: str = ""
) -> dict | None:
    gold_range = _line_range(gold)
    predicted_range = _line_range(predicted)
    if predicted_range is None and source_body:
        predicted_range = _span_line_range(predicted, source_body)
    if gold_range is None or predicted_range is None:
        return None
    intersection = max(
        0,
        min(gold_range[1], predicted_range[1])
        - max(gold_range[0], predicted_range[0])
        + 1,
    )
    union = (
        max(gold_range[1], predicted_range[1])
        - min(gold_range[0], predicted_range[0])
        + 1
    )
    return {
        "gold": list(gold_range),
        "predicted": list(predicted_range),
        "intersection_lines": intersection,
        "iou": round(intersection / union, 4),
    }


def _candidate(
    gold_index: int,
    predicted_index: int,
    gold: dict,
    predicted: dict,
    source_body: str = "",
    threshold: float = MATCH_THRESHOLD,
) -> dict:
    title_score, title_terms = _overlap(gold.get("title"), predicted.get("title"))
    subject_score, subject_terms = _overlap(
        gold.get("subject"), predicted.get("subject")
    )
    lexical_score = 0.7 * title_score + 0.3 * subject_score
    semantic_score = max(lexical_score, title_score, subject_score)
    boundary = _boundary_evidence(gold, predicted, source_body)
    # Boundaries disambiguate semantically plausible candidates; they can never
    # manufacture a match between accounts about different events.
    score = semantic_score + (boundary["iou"] if boundary else 0.0)
    return {
        "gold_index": gold_index,
        "predicted_index": predicted_index,
        "score": round(score, 4),
        "title_score": round(title_score, 4),
        "subject_score": round(subject_score, 4),
        "lexical_score": round(lexical_score, 4),
        "semantic_score": round(semantic_score, 4),
        "shared_title_terms": title_terms,
        "shared_subject_terms": subject_terms,
        "boundary_overlap": boundary,
        "eligible": semantic_score >= threshold
        and (boundary is None or boundary["intersection_lines"] > 0),
    }


def _optimal_matches(
    candidates: list[dict], gold_count: int, predicted_count: int
) -> list[dict]:
    """Maximum-cardinality, then maximum-score deterministic assignment."""
    if not gold_count or not predicted_count:
        return []
    by_pair = {
        (candidate["predicted_index"], candidate["gold_index"]): candidate
        for candidate in candidates
    }
    columns = gold_count + predicted_count  # one unmatched slot per prediction
    costs = []
    for predicted_index in range(predicted_count):
        row = []
        for gold_index in range(columns):
            candidate = by_pair.get((predicted_index, gold_index))
            weight = (
                1000.0 + candidate["score"]
                if candidate is not None and candidate["eligible"]
                else 0.0
            )
            row.append(-weight)
        costs.append(row)

    # Rectangular Hungarian algorithm (rows <= columns). Iteration order is the
    # tie-break, so equal optima resolve by prediction index then gold index.
    row_potential = [0.0] * (predicted_count + 1)
    column_potential = [0.0] * (columns + 1)
    matched_row = [0] * (columns + 1)
    predecessor = [0] * (columns + 1)
    for row_index in range(1, predicted_count + 1):
        matched_row[0] = row_index
        minimum = [float("inf")] * (columns + 1)
        used = [False] * (columns + 1)
        column = 0
        while True:
            used[column] = True
            active_row = matched_row[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, columns + 1):
                if used[candidate_column]:
                    continue
                reduced = (
                    costs[active_row - 1][candidate_column - 1]
                    - row_potential[active_row]
                    - column_potential[candidate_column]
                )
                if reduced < minimum[candidate_column]:
                    minimum[candidate_column] = reduced
                    predecessor[candidate_column] = column
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(columns + 1):
                if used[candidate_column]:
                    row_potential[matched_row[candidate_column]] += delta
                    column_potential[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while column:
            previous = predecessor[column]
            matched_row[column] = matched_row[previous]
            column = previous

    selected = []
    for column in range(1, gold_count + 1):
        if matched_row[column]:
            candidate = by_pair[(matched_row[column] - 1, column - 1)]
            if candidate["eligible"]:
                selected.append(candidate)
    return selected


def score(
    gold_document: dict,
    predicted_document: dict,
    threshold: float = MATCH_THRESHOLD,
    source_body: str = "",
) -> dict:
    """Score accounts using semantic-constrained optimal one-to-one matching."""
    gold = gold_document.get("accounts") or []
    predicted = predicted_document.get("accounts") or []
    candidates = [
        _candidate(gi, pi, g, p, source_body, threshold)
        for gi, g in enumerate(gold)
        for pi, p in enumerate(predicted)
    ]
    selected = _optimal_matches(candidates, len(gold), len(predicted))
    used_gold = {candidate["gold_index"] for candidate in selected}
    used_predicted = {candidate["predicted_index"] for candidate in selected}
    matches = [
        {
            **candidate,
            "gold_title": gold[candidate["gold_index"]].get("title", ""),
            "predicted_title": predicted[candidate["predicted_index"]].get("title", ""),
        }
        for candidate in selected
    ]
    matches.sort(key=lambda row: row["gold_index"])

    boundaries = [
        m["boundary_overlap"]["iou"] for m in matches if m["boundary_overlap"]
    ]
    binding = predicted_document.get("binding") or {}
    bound = binding.get("bound", 0)
    unbindable = binding.get("unbindable", 0)
    outside = binding.get("outside", 0)
    counts_are_valid = all(
        isinstance(n, int) and n >= 0 for n in (bound, unbindable, outside)
    )
    binding_total = bound + unbindable + outside if counts_are_valid else 0

    return {
        "method": {
            "matching": "deterministic maximum-cardinality, maximum-score one-to-one assignment; boundaries rank only candidates that pass semantic matching",
            "semantic_threshold": threshold,
        },
        "counts": {
            "gold": len(gold),
            "predicted": len(predicted),
            "matched": len(matches),
        },
        "precision": round(len(matches) / len(predicted), 4) if predicted else None,
        "recall": round(len(matches) / len(gold), 4) if gold else None,
        "boundary_overlap": {
            "available": len(boundaries),
            "matched": len(matches),
            "mean_iou": round(sum(boundaries) / len(boundaries), 4)
            if boundaries
            else None,
        },
        "claim_binding": {
            "bound": bound,
            "unbindable": unbindable,
            "outside": outside,
            "total": binding_total,
            "coverage": round(bound / binding_total, 4) if binding_total else None,
            "resolvable_coverage": round(bound / (bound + outside), 4)
            if counts_are_valid and bound + outside
            else None,
        },
        "matches": matches,
        "missed": [
            {"gold_index": i, "title": account.get("title", "")}
            for i, account in enumerate(gold)
            if i not in used_gold
        ],
        "extra": [
            {
                "predicted_index": i,
                "id": account.get("id"),
                "title": account.get("title", ""),
            }
            for i, account in enumerate(predicted)
            if i not in used_predicted
        ],
    }


def markdown_report(
    result: dict, gold_path: Path, predicted_path: Path, source_path: Path | None = None
) -> str:
    counts = result["counts"]
    boundary = result["boundary_overlap"]
    binding = result["claim_binding"]
    lines = [
        "# Account extraction baseline",
        "",
        f"- Ground truth: `{gold_path}`",
        f"- Prediction: `{predicted_path}`",
        f"- Boundary source: `{source_path}`"
        if source_path
        else "- Boundary source: not supplied",
        f"- Matching: {result['method']['matching']}",
        f"- Semantic threshold: {result['method']['semantic_threshold']}",
        f"- Precision: {result['precision']} ({counts['matched']}/{counts['predicted']})",
        f"- Recall: {result['recall']} ({counts['matched']}/{counts['gold']})",
        f"- Boundary overlap: {boundary['mean_iou']} mean intersection-over-union ({boundary['available']}/{boundary['matched']} matches measurable)",
        f"- Claim-binding coverage: {binding['coverage']} ({binding['bound']}/{binding['total']})",
        f"- Resolvable claim-binding coverage: {binding['resolvable_coverage']} ({binding['bound']}/{binding['bound'] + binding['outside']})",
        "",
        "## Matches",
        "",
        "| Gold | Predicted | Semantic | Matching evidence | Boundary IoU | Assignment score |",
        "|---|---|---:|---|---:|---:|",
    ]
    for match in result["matches"]:
        evidence = sorted(
            set(match["shared_title_terms"] + match["shared_subject_terms"])
        )
        overlap = match["boundary_overlap"]
        lines.append(
            f"| {match['gold_title']} | {match['predicted_title']} | "
            f"{match['semantic_score']:.4f} | {', '.join(evidence) or '(none)'} | "
            f"{overlap['iou'] if overlap else 'n/a'} | {match['score']:.4f} |"
        )
    lines.extend(["", "## Missed", ""])
    lines.extend(f"- {row['title']}" for row in result["missed"])
    lines.extend(["", "## Extra", ""])
    lines.extend(f"- {row['title']}" for row in result["extra"])
    lines.extend(
        [
            "",
            "## Machine-readable result",
            "",
            "```json",
            json.dumps(result, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold", type=Path)
    parser.add_argument("predicted", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source_body = ""
    if args.source:
        from anomalica_common.pre_digest import materialise

        from digester.record_parser import parse_record

        source_body = materialise(parse_record(args.source.read_text()).body)
    result = score(_load(args.gold), _load(args.predicted), source_body=source_body)
    report = markdown_report(result, args.gold, args.predicted, args.source)
    if args.output:
        args.output.write_text(report)
    else:
        print(report)


if __name__ == "__main__":
    main()
