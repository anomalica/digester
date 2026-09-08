#!/usr/bin/env python3
"""Score the model AND both automatic instruments against the hand-labelled set.

The point is not the model's number. It is that `sole_reference_score` and
`subject_first_score` each ASSUME the right answer is `subject`, and until now
nothing had checked that assumption against a human reading. This scores the
instruments themselves.

    python3 reports/salience/score_labels.py
"""

from __future__ import annotations

import glob
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workspace"))
sys.path.insert(0, "/home/mark/repos/anomalica/anomalica-common/src")

from digester import salience  # noqa: E402

LABELS = Path(__file__).with_name("hand-labels.yaml")


def sampled_pairs(record: str, variant: str, seed: int = 5, n: int = 40):
    hits = glob.glob(
        f"/home/mark/repos/anomalica/digests/variants/{record}*/{variant}*.yaml"
    )
    if not hits:
        raise SystemExit(f"no digest on disk for {record}/{variant}")
    d = yaml.safe_load(open(hits[0]))
    claims = [
        c
        for c in salience.claims_of(d)
        if salience._refs(c) and (c.get("text") or "").strip()
    ]
    pairs = [(c, r) for c in claims for r in salience._refs(c)]
    random.seed(seed)
    return random.sample(pairs, n)


def main() -> int:
    spec = yaml.safe_load(open(LABELS))
    labels = {p["i"]: p for p in spec["pairs"]}
    pairs = sampled_pairs(spec["record"], spec["digest"])

    rows = []
    for i, (claim, ref) in enumerate(pairs):
        lab = labels.get(i)
        if lab is None:
            continue
        if (ref.get("name") or "") != lab["ref"]:
            raise SystemExit(
                f"sample drifted at i={i}: digest has {ref.get('name')!r}, "
                f"labels have {lab['ref']!r}. The digest was regenerated; "
                f"re-label rather than trusting the join."
            )
        rows.append(
            {
                "i": i,
                "gold": lab["gold"],
                "model": ref.get("role"),
                "ambiguous": bool(lab.get("ambiguous")),
                "sole": len(salience._refs(claim)) == 1,
                "subject_first": _subject_first(claim, ref),
                "ref": lab["ref"],
                "text": (claim.get("text") or "")[:80],
            }
        )

    print(f"hand-labelled pairs: {len(rows)}\n")
    _model(rows)
    _instrument(rows, "sole", "sole_reference_score")
    _instrument(rows, "subject_first", "subject_first_score")
    _confusion(rows)
    _power(rows)
    _stratified()
    return 0


def _stratified() -> None:
    """The instrument's assumption, on a sample drawn from what it selects.

    The random set contains too few sole-reference edges to calibrate the
    instrument, so a second file samples that population directly. It answers a
    different question and must not be mixed into the accuracy figure above:
    it is biased towards exactly the claims where the assumption fails.
    """
    path = LABELS.with_name("hand-labels-sole.yaml")
    if not path.exists():
        return
    pairs = yaml.safe_load(path.read_text())["pairs"]
    right = [p for p in pairs if p["gold"] == "subject"]
    wrong_model = [p for p in pairs if p["gold"] != p["model"]]
    print(
        f"\nsole_reference_score, calibrated on {len(pairs)} edges drawn FROM ITS OWN SELECTION"
    )
    print(
        f"  its assumption (gold=subject) holds on {len(right)}/{len(pairs)}"
        f" = {len(right) / len(pairs):.0%}"
    )
    print(
        f"  so about {1 - len(right) / len(pairs):.0%} of what it scores is a claim whose"
        " subject was never extracted"
    )
    print(
        f"  model role accuracy on this sample: {len(pairs) - len(wrong_model)}/{len(pairs)}"
        f" = {1 - len(wrong_model) / len(pairs):.0%} - NOT a general figure, the sample is biased"
    )


def _subject_first(claim: dict, ref: dict, window: int = 60) -> bool:
    """The predicate subject_first_score uses, lifted verbatim."""
    if len(salience._refs(claim)) != 1:
        return False
    name, text = ref.get("name") or "", (claim.get("text") or "").strip()
    if not name or not text:
        return False
    first = name.split(",")[0].split("(")[0].strip().lower()
    return bool(first) and first in text[:window].lower()


def _model(rows: list[dict]) -> None:
    strict = [r for r in rows]
    clean = [r for r in rows if not r["ambiguous"]]
    for name, rs in (("all pairs", strict), ("unambiguous only", clean)):
        wrong = [r for r in rs if r["model"] != r["gold"]]
        print(
            f"MODEL role accuracy, {name:17}: "
            f"{len(rs) - len(wrong)}/{len(rs)} = {(1 - len(wrong) / len(rs)):.0%}"
        )
    print()
    for r in rows:
        if r["model"] != r["gold"]:
            flag = " (ambiguous)" if r["ambiguous"] else ""
            print(
                f"  i={r['i']:2} {r['model']:11} -> gold {r['gold']:11}{flag}  {r['ref'][:44]}"
            )
    print()


def _instrument(rows: list[dict], key: str, name: str) -> None:
    """An instrument's assumed answer is `subject` on every edge it selects."""
    sel = [r for r in rows if r[key]]
    if not sel:
        print(f"{name}: selects no pair in this sample\n")
        return
    assumption_right = [r for r in sel if r["gold"] == "subject"]
    flagged = [r for r in sel if r["model"] != "subject"]
    truly_wrong = [r for r in flagged if r["model"] != r["gold"]]
    print(f"{name}")
    print(f"  edges selected in the sample : {len(sel)}")
    print(
        f"  its assumption (gold=subject) : right on {len(assumption_right)}/{len(sel)}"
        f" = {len(assumption_right) / len(sel):.0%}"
    )
    print(f"  edges it calls wrong          : {len(flagged)}")
    print(f"  of those, wrong by hand       : {len(truly_wrong)}")
    if flagged:
        print(
            f"  SPURIOUS ERROR RATE           : "
            f"{1 - len(truly_wrong) / len(flagged):.0%} of what it reports is not an error"
        )
    print()


def _confusion(rows: list[dict]) -> None:
    conf: Counter = Counter((r["gold"], r["model"]) for r in rows)
    roles = salience.ROLES
    print("confusion (rows gold, columns model)")
    print(f"  {'':12}" + "".join(f"{c:>12}" for c in roles))
    for g in roles:
        print(f"  {g:12}" + "".join(f"{conf.get((g, m), 0):>12}" for m in roles))
    print()


def _power(rows: list[dict]) -> None:
    """What a second arm would need to be worth labelling."""
    acc = sum(r["model"] == r["gold"] for r in rows) / len(rows)
    half = 1.96 * (acc * (1 - acc) / len(rows)) ** 0.5
    print(f"accuracy {acc:.0%}, 95% interval +/-{half:.0%} on {len(rows)} pairs")
    for delta in (0.10, 0.05):
        n = 2 * (1.96**2) * acc * (1 - acc) / delta**2
        print(
            f"  to resolve a {delta:.0%} difference between two arms: ~{n:.0f} labelled pairs PER ARM"
        )


if __name__ == "__main__":
    raise SystemExit(main())
