"""Per-claim entailment: does the source warrant the claim as written?

A local natural-language-inference classifier reads each claim's verbatim
`quote` as premise and its `text` as hypothesis and records
``entailment: {label, score, model, premise}`` on the claim. No model calls,
no allowance, no dollars; a few seconds of GPU per digest.

TWO STAGES, because the quote alone licenses about a quarter of good claims.
Measured on 50 hand-labelled claims (2026-09-02): the base model reads 32 of 45
good claims as neutral at p 0.9-1.0, not because they are wrong but because a
claim text is written self-contained - it names the speaker, expands the
acronym, resolves "that document" - and the bare quote carries none of that.
Prefixing the speaker recovers seven. Giving the model the record text 800
characters either side of the located quote recovers 38 of 45 while still
catching 11 of 12 mutated contradictions. So:

  1. premise = speaker + quote. `entails` or `contradicts` is final.
  2. only a `neutral` goes on, with premise = speaker + record window.

`premise` records which text produced the verdict. entails/window is the
weaker verdict: the quote shown to a reader does not carry the claim on its
own, the surrounding record does. neutral means "not warranted even by the
surrounding record". A quote that cannot be located gets stage 1 only.

On 34 constructed true neutrals (invented years, inferred motives, merged
sources) the pipeline keeps 27 out of entails; the seven that slip through
all score under 0.42, so the review order (entails/window ascending by score)
puts them first. No confidence gate is applied: one at 0.5 would hold every
one of those seven back at the cost of nine good claims in the same band.

The base model takes stage 1 (it catches a clear contradiction the large one
reads as neutral) and the large one stage 2 (better on long premises). Both
are MIT, three-way, fit on the 6 GB card beside the transcription service,
and are the `check` stage's priority list in the model policy - resolved
through it before loading, and refused if the policy refuses.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path

LABELS = ("entails", "neutral", "contradicts")
STAGE = "check"
STAGE1_MODEL = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
STAGE2_MODEL = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"
WINDOW_CHARS = 800
# Tokens, premise + hypothesis. The largest quote in the corpus is 808
# characters (~200 tokens); the window is what gets truncated, never the claim.
MAX_TOKENS = 512
CLAIM_LISTS = ("domain_claims", "infrastructure_claims")

_CHAR_SPAN = re.compile(r"char:(\d+)-(\d+)")


def enabled() -> bool:
    """`DIGESTER_ENTAILMENT=off` skips the extract-time step (benchmarks, tests)."""
    return os.environ.get("DIGESTER_ENTAILMENT", "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def policy_refusal(*models: str) -> str | None:
    """Why the model policy refuses one of these models for the check stage.

    Fail closed: an unreadable policy raises, exactly as it does for every
    other stage. None means all permitted.
    """
    from anomalica_common import model_policy

    policy = model_policy.load()
    for m in models:
        why = policy.refusal(STAGE, m)
        if why:
            return f"{m}: {why}"
    return None


def _normalise(s: str) -> str:
    return re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', s or ""))


def locate(pre_digest: str, quote: str, location: str | None) -> tuple[int, int] | None:
    """(start, end) of the quote in the pre-digest, or None.

    The first verbatim fragment is searched for (an elided quote's fragments
    are each verbatim; the whole is not), falling back to a `char:` location
    the re-aligner already wrote. Curly quotes are folded first, matching the
    fidelity check's lesson that they cost 76 false misses.
    """
    text = _normalise(pre_digest)
    q = _normalise(quote)
    frag = q.split("...")[0].strip()
    if frag:
        i = text.find(frag[:80])
        if i >= 0:
            return i, i + len(q)
        # The pre-digest breaks lines where the quote has spaces (a sentence
        # per line in transcripts, wrapped paragraphs in ebooks); 13.6% of the
        # corpus failed the exact search on that alone. Match the first tokens
        # across any whitespace.
        tokens = frag.split()[:12]
        if tokens:
            m = re.search(r"\s+".join(re.escape(t) for t in tokens), text)
            if m:
                return m.start(), m.start() + len(q)
    m = _CHAR_SPAN.search(location or "")
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a < b <= len(text):
            return a, b
    return None


def window(pre_digest: str, span: tuple[int, int], chars: int = WINDOW_CHARS) -> str:
    a, b = span
    return _normalise(pre_digest)[max(0, a - chars) : b + chars]


def _premise(speaker: str | None, body: str) -> str:
    return f"{speaker}: {body}" if speaker else body


class Classifier:
    """One NLI checkpoint, loaded on first use. Probabilities in LABELS order.

    `seconds` accumulates wall time spent classifying (loading included), the
    figure the usage ledger records for a local stage.
    """

    def __init__(self, model_id: str, device: str | None = None, batch_size: int = 32):
        self.model_id = model_id
        self.device = device or os.environ.get("DIGESTER_ENTAILMENT_DEVICE")
        self.batch_size = batch_size
        self.seconds = 0.0
        self._model = None
        self._tok = None

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tok = AutoTokenizer.from_pretrained(self.model_id)
        model = AutoModelForSequenceClassification.from_pretrained(self.model_id)
        if self.device.startswith("cuda"):
            model = model.half()
        self._model = model.to(self.device).eval()
        got = [self._model.config.id2label[i].lower() for i in range(3)]
        want = ["entailment", "neutral", "contradiction"]
        if got != want:
            raise RuntimeError(f"{self.model_id} labels {got}, expected {want}")

    def probs(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        if not pairs:
            return []
        t0 = time.monotonic()
        if self._model is None:
            self._load()
        import torch

        # Length-sorted batches: padding to the longest pair in a batch is the
        # cost, and a corpus mixes 40-character quotes with 1,600-character
        # windows. Results are restored to input order.
        order = sorted(
            range(len(pairs)), key=lambda k: len(pairs[k][0]) + len(pairs[k][1])
        )
        out: list[list[float] | None] = [None] * len(pairs)
        i = 0
        while i < len(order):
            idx = order[i : i + self.batch_size]
            try:
                rows = self._run_batch([pairs[k] for k in idx])
            except torch.cuda.OutOfMemoryError:
                # The card is shared with the transcription service, and a
                # 512-token batch on the large model can exceed what is left.
                # Halve and retry; at one pair per batch, move to the CPU
                # rather than fail the digest.
                torch.cuda.empty_cache()
                if self.batch_size > 1:
                    self.batch_size //= 2
                    continue
                self._to_cpu()
                continue
            for k, row in zip(idx, rows):
                out[k] = row
            i += len(idx)
        self.seconds += time.monotonic() - t0
        return out  # type: ignore[return-value]

    def _run_batch(self, batch: list[tuple[str, str]]) -> list[list[float]]:
        import torch

        x = self._tok(
            [p for p, _ in batch],
            [h for _, h in batch],
            truncation="only_first",
            max_length=MAX_TOKENS,
            padding=True,
            return_tensors="pt",
        ).to(self.device)
        with torch.no_grad():
            p = torch.softmax(self._model(**x).logits.float(), -1).cpu()
        return [row.tolist() for row in p]

    def _to_cpu(self) -> None:
        import torch

        self.device = "cpu"
        self._model = self._model.float().to("cpu")
        torch.cuda.empty_cache()

    def release(self) -> None:
        self._model = None
        self._tok = None
        try:
            import torch

            torch.cuda.empty_cache()
        except ImportError:
            pass


Probs = Callable[[list[tuple[str, str]]], list[list[float]]]


def _verdict(row: list[float]) -> tuple[str, float]:
    i = max(range(3), key=lambda k: row[k])
    return LABELS[i], round(float(row[i]), 3)


def _eligible(claim: dict) -> bool:
    return bool(
        isinstance(claim, dict)
        and isinstance(claim.get("quote"), str)
        and claim["quote"].strip()
        and isinstance(claim.get("text"), str)
        and claim["text"].strip()
    )


def _assessed(claim: dict) -> bool:
    e = claim.get("entailment")
    return isinstance(e, dict) and e.get("label") in LABELS


def needs_check(doc: dict) -> bool:
    """Whether a loaded digest has an eligible claim without a verdict.

    The scheduler's queue test, derived from the artefact and not from a
    list: any claim with a quote and a text and no valid entailment block.
    """
    return any(
        _eligible(c) and not _assessed(c)
        for key in CLAIM_LISTS
        for c in doc.get(key) or []
    )


def annotate(
    doc: dict,
    pre_digest: str | None,
    stage1: Probs,
    stage2: Probs | None,
    *,
    stage1_model: str = STAGE1_MODEL,
    stage2_model: str = STAGE2_MODEL,
    force: bool = False,
    redo_unlocated: bool = False,
) -> dict:
    """Write `entailment` onto every eligible claim of a loaded digest.

    Returns counts: assessed, skipped (already carried a verdict), ineligible
    (no quote or no text), unlocated (stage 1 only), and the label mix by
    premise. Pure over its inputs apart from the mutation of `doc`; the
    classifiers are injected so tests need no model.

    `redo_unlocated` takes only the claims left at neutral/quote by an
    earlier pass - stage 1 said neutral and the quote was not located - and
    gives them stage 2 now that the locator has improved. Stage 1 is
    deterministic, so its verdict stands and is not re-run.
    """
    counts = {
        "assessed": 0,
        "skipped": 0,
        "ineligible": 0,
        "unlocated": 0,
        "labels": {},
    }
    todo: list[dict] = []
    for key in CLAIM_LISTS:
        for c in doc.get(key) or []:
            if not _eligible(c):
                counts["ineligible"] += 1
                continue
            if redo_unlocated:
                e = c.get("entailment") or {}
                if e.get("label") == "neutral" and e.get("premise") == "quote":
                    todo.append(c)
                else:
                    counts["skipped"] += 1
                continue
            if _assessed(c) and not force:
                counts["skipped"] += 1
                continue
            todo.append(c)
    if not todo:
        return counts

    def speaker(c: dict) -> str | None:
        s = c.get("speaker")
        return s.get("name") if isinstance(s, dict) else None

    second: list[dict] = []
    if redo_unlocated:
        second = list(todo)
    else:
        first = stage1([(_premise(speaker(c), c["quote"]), c["text"]) for c in todo])
        for c, row in zip(todo, first):
            label, score = _verdict(row)
            c["entailment"] = {
                "label": label,
                "score": score,
                "model": stage1_model,
                "premise": "quote",
            }
            if label == "neutral":
                second.append(c)

    if second and stage2 is not None and pre_digest:
        pairs, targets = [], []
        for c in second:
            span = locate(pre_digest, c["quote"], c.get("location"))
            if span is None:
                counts["unlocated"] += 1
                continue
            pairs.append((_premise(speaker(c), window(pre_digest, span)), c["text"]))
            targets.append(c)
        for c, row in zip(targets, stage2(pairs)):
            label, score = _verdict(row)
            c["entailment"] = {
                "label": label,
                "score": score,
                "model": stage2_model,
                "premise": "window",
            }
    elif second:
        counts["unlocated"] += len(second)

    for c in todo:
        if redo_unlocated and c["entailment"].get("premise") != "window":
            continue  # still unlocated: the earlier verdict stands, nothing new
        counts["assessed"] += 1
        k = f"{c['entailment']['label']}/{c['entailment']['premise']}"
        counts["labels"][k] = counts["labels"].get(k, 0) + 1
    return counts


class Checker:
    """The two classifiers, policy-checked, loaded once, shared across a batch."""

    def __init__(
        self,
        device: str | None = None,
        stage1_model: str = STAGE1_MODEL,
        stage2_model: str = STAGE2_MODEL,
    ):
        why = policy_refusal(stage1_model, stage2_model)
        if why:
            raise PermissionError(f"model policy refuses the {STAGE} stage: {why}")
        self.stage1 = Classifier(stage1_model, device)
        self.stage2 = Classifier(stage2_model, device)

    def annotate(
        self,
        doc: dict,
        pre_digest: str | None,
        force: bool = False,
        redo_unlocated: bool = False,
    ) -> dict:
        s1, s2 = self.stage1.seconds, self.stage2.seconds
        counts = annotate(
            doc,
            pre_digest,
            self.stage1.probs,
            self.stage2.probs,
            stage1_model=self.stage1.model_id,
            stage2_model=self.stage2.model_id,
            force=force,
            redo_unlocated=redo_unlocated,
        )
        counts["duration_s"] = round(
            (self.stage1.seconds - s1) + (self.stage2.seconds - s2), 1
        )
        counts["models"] = [self.stage1.model_id, self.stage2.model_id]
        return counts

    def usage_entries(self, duration_s: float) -> list[dict]:
        """The digest's ai_usage entries for one check run (ADR 0037 local shape).

        One entry per stage model, the wall time split in proportion to what
        each classifier has spent so far; a local stage carries duration, not
        tokens, and no cost.
        """
        from anomalica_common.llm.provenance import local_entry

        total = self.stage1.seconds + self.stage2.seconds
        share = (self.stage1.seconds / total) if total else 1.0
        return [
            local_entry(STAGE, self.stage1.model_id, round(duration_s * share, 1)),
            local_entry(
                STAGE, self.stage2.model_id, round(duration_s * (1 - share), 1)
            ),
        ]

    def release(self) -> None:
        self.stage1.release()
        self.stage2.release()


def annotate_yaml(
    text: str,
    pre_digest: str | None,
    checker: Checker,
    force: bool = False,
    redo_unlocated: bool = False,
) -> tuple[str, dict]:
    """Annotate a serialised digest. Returns (new text, counts).

    Loads, annotates, appends the check's ai_usage entries, re-dumps with the
    digest writer's own dumper, and parses the result before handing it back -
    the refresh path's rule: fail loudly rather than write junk.
    """
    import yaml
    from anomalica_common.digest.yaml_format import _yaml_dump

    from digester.health import _Loader

    doc = yaml.load(text, Loader=_Loader)
    if not isinstance(doc, dict):
        raise ValueError("digest is not a mapping")
    counts = checker.annotate(
        doc, pre_digest, force=force, redo_unlocated=redo_unlocated
    )
    if not counts["assessed"]:
        return text, counts
    usage = doc.get("ai_usage")
    if not isinstance(usage, list):
        usage = []
    doc["ai_usage"] = usage + checker.usage_entries(counts.get("duration_s", 0.0))
    out = _yaml_dump(doc)
    yaml.load(out, Loader=_Loader)
    return out, counts


def pre_digest_for(doc: dict, records_dir: Path) -> str | None:
    """The materialised record a digest was built from, by declared hash."""
    from anomalica_common.pre_digest import materialise

    from digester.health import _records_by_hash
    from digester.record_parser import parse_record

    h = ((doc.get("record") or {}).get("content_hash") or "").split(":")[-1]
    if not h:
        return None
    store = records_dir.parent / "store"
    candidates = [store / f"{h}.md", store / f"{h}.v2.md"]
    rec = next((p for p in candidates if p.is_file()), None) or _records_by_hash(
        records_dir
    ).get(h)
    if rec is None:
        return None
    try:
        return materialise(parse_record(rec.read_text(errors="replace")).body)
    except (OSError, ValueError):
        return None
