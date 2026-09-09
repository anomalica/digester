"""The two ingest records the pipeline tests run through digestion and import.

Both are invented. Nothing here is drawn from the live corpus: a fixture that
reads like real material eventually gets ingested as real material.

What each document is for:

Document A - `anomalica/record/2`, source_type video, inline `{{t:SECONDS}}`
word tokens, a review sidecar, a copyright block, and the whole provenance
ladder in one record: a speaker chain with an empty relay (first_hand), a named
origin with an empty relay and an anonymous origin relayed once (both
second_hand), a document origin relayed twice (third_hand), and an unattributed
chain (no attestation at all). Two DISTINCT anonymous origins - `duty-officer-1`
and `colleague-1` - both assert something about the same node, which is what
turns a lost `origin_ref` column from a silent absence into a wrong
independence count. One claim declares an attestation that contradicts its own
chain, and one names a bracketed description as its speaker, which the importer
must rewrite to an anonymous origin.

Document B - `anomalica/record/1`, source_type pdf, no review sidecar, no
copyright block (its absence once let a serialisation TypeError survive a green
suite), a frontmatter key in neither `health.MAPPED_RECORD_FIELDS` nor
`UNWANTED_RECORD_FIELDS`, two infrastructure-category claims so the second
claim list is written, and one claim whose quote is deliberately not verbatim so
the unalignable branch of location normalisation runs. It shares
`Northern Reach Observatory` and `Dr Helena Marsh` with document A at the exact
tier and `Coastal Air Defence Command (CADC)` at the acronym tier.

Each store file is named by the sha256 of its own body and declares that same
value as `content_hash`, so `assimilator.import_markdown._content_hash_of`
agrees with the frontmatter instead of warning; `by-name/` holds real symlinks
so the friendly-name resolution path is exercised rather than simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
INGESTS_DIR = FIXTURES_DIR / "ingests"
STORE_DIR = INGESTS_DIR / "store"
BY_NAME_DIR = INGESTS_DIR / "by-name"


@dataclass(frozen=True)
class DocumentFixture:
    """One fixture ingest, addressed the way the live store addresses records."""

    key: str
    content_hash: str
    store_name: str
    friendly_name: str
    marker: str

    @property
    def store_path(self) -> Path:
        return STORE_DIR / self.store_name

    @property
    def by_name_path(self) -> Path:
        return BY_NAME_DIR / f"{self.friendly_name}.md"

    @property
    def review_sidecar(self) -> Path:
        return STORE_DIR / f"{self.content_hash}.review.json"

    def read(self) -> str:
        return self.store_path.read_text()

    def parsed(self):
        from digester.record_parser import parse_record

        return parse_record(self.read())


DOCUMENT_A = DocumentFixture(
    key="a",
    content_hash="600191de7e90695793f5eea31c91d15c9ae6636a2e63be17d3a5702c7b253df6",
    store_name="600191de7e90695793f5eea31c91d15c9ae6636a2e63be17d3a5702c7b253df6.v2.md",
    friendly_name="skerrivore-point-nro-interview",
    # Present in A's body and absent from B's, so a stubbed model call can tell
    # which document it was handed without depending on chunking.
    marker="Ivo Rennick",
)

DOCUMENT_B = DocumentFixture(
    key="b",
    content_hash="36f052eda6b28fa2843979c816e9405626a8ff6d5ebcd9c31b9ede4babc56277",
    store_name="36f052eda6b28fa2843979c816e9405626a8ff6d5ebcd9c31b9ede4babc56277.md",
    friendly_name="nro-note-skerrivore-enquiry",
    marker="Marion Kilbride",
)

DOCUMENTS = {DOCUMENT_A.key: DOCUMENT_A, DOCUMENT_B.key: DOCUMENT_B}


def check_layout() -> None:
    """Fail loudly if the store on disk has drifted from what these constants say.

    The declaration and the filename are what `_content_hash_of` compares; a
    hand-edit that renames one and not the other otherwise shows up much later
    as a warning line nobody reads.
    """
    import yaml

    for doc in DOCUMENTS.values():
        if not doc.store_path.exists():
            raise AssertionError(f"fixture record missing: {doc.store_path}")
        if not doc.by_name_path.is_symlink():
            raise AssertionError(f"by-name symlink missing: {doc.by_name_path}")
        if doc.by_name_path.resolve() != doc.store_path.resolve():
            raise AssertionError(
                f"{doc.by_name_path} does not point at {doc.store_path}"
            )
        if not doc.store_name.startswith(doc.content_hash):
            raise AssertionError(
                f"{doc.store_name} is not named by its declared hash {doc.content_hash}"
            )
        front = yaml.safe_load(doc.read().split("---", 2)[1])
        declared = front.get("content_hash")
        if declared != f"sha256:{doc.content_hash}":
            raise AssertionError(
                f"{doc.store_name} declares {declared!r}, expected "
                f"'sha256:{doc.content_hash}'"
            )
