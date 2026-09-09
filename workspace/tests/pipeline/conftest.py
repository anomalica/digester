"""Spend guard, determinism pins and fixture wiring for the digestion-stage tests.

The guard itself and the determinism pins live in `fixtures/harness.py`, shared
with the assimilator's end-to-end module so there is one definition of what a
model call is blocked from doing. Read that file for how the layering works.

Environment is set at IMPORT time, not through `monkeypatch`, because the
transport flushes its ledger row at interpreter exit - long after any fixture
has torn down. `ledger.enabled()` has a `PYTEST_CURRENT_TEST` guard, and that
variable is NOT set during the atexit flush the guard was written for, so
pointing `SCHEDULER_DISPATCH_LOG` at a temporary file is what actually keeps the
production spend ledger clean.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from .fixtures import documents as fixture_documents
from .fixtures import responses as fixture_responses
from .fixtures.harness import (  # noqa: F401 - re-exported for the test modules
    GUARDED_TRANSPORT_CALLS,
    CountingUuid,
    FrozenDatetime,
    FROZEN_INSTANT,
    ProviderCallAttempted,
    install_spend_guard,
)

# --- Import-time environment ------------------------------------------------
_SANDBOX = Path(tempfile.mkdtemp(prefix="anomalica-pipeline-tests-"))

os.environ["SCHEDULER_DISPATCH_LOG"] = str(_SANDBOX / "model-dispatch.jsonl")
os.environ["ANOMALICA_LEDGER"] = "off"
os.environ["DIGESTER_CALL_CACHE"] = "off"
os.environ["DIGESTER_ENTAILMENT"] = "off"
os.environ["ANOMALICA_CURATION_DIR"] = str(_SANDBOX / "curation")
os.environ["ASSIMILATOR_DATA_DIR"] = str(_SANDBOX / "assimilator")
os.environ["ASSIMILATOR_DB"] = str(_SANDBOX / "assimilator" / "knowledge.db")
os.environ["ANOMALICA_INGESTS_DIR"] = str(fixture_documents.INGESTS_DIR)
os.environ["DIGESTER_USE_API"] = "0"
os.environ["ANOMALICA_USE_API"] = "0"

for _key in [k for k in os.environ if k.endswith("_API_KEY")]:
    del os.environ[_key]

(_SANDBOX / "curation").mkdir(parents=True, exist_ok=True)
(_SANDBOX / "assimilator").mkdir(parents=True, exist_ok=True)


def sandbox_dir() -> Path:
    """The session's scratch directory, for anything that must not touch $HOME."""
    return _SANDBOX


# --- The spend guard --------------------------------------------------------


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    """Make a real model call impossible, by every route the code has."""
    install_spend_guard(monkeypatch)


@pytest.fixture(autouse=True)
def allowance_granted(monkeypatch):
    """Skip the scheduler's usage check.

    Left alone, `digester extract` makes three urlopen attempts to
    127.0.0.1:8001 with 4.5 seconds of sleeps between them and then fails
    closed - so the command under test never runs and the suite is slow about it.
    """
    from anomalica_common.llm.allowance import Allowance
    from digester import cli

    monkeypatch.setattr(
        cli,
        "check_allowance",
        lambda **_: Allowance(ok=True, reason="pinned by the pipeline tests"),
    )


# --- Determinism ------------------------------------------------------------


@pytest.fixture
def frozen_digest_ids(monkeypatch) -> CountingUuid:
    """Pin the two sources of non-determinism in a written digest.

    Every id in a digest is a fresh `uuid4` and `extracted_at` is
    `datetime.now`, so two runs over identical input differ in every id and one
    timestamp. Both are minted in `anomalica_common.digest.yaml_format`, which
    is the single place to pin them.
    """
    from anomalica_common.digest import yaml_format

    counter = CountingUuid()
    monkeypatch.setattr(yaml_format, "uuid", counter)
    monkeypatch.setattr(yaml_format, "datetime", FrozenDatetime)
    return counter


# --- The fixture corpus -----------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _fixture_corpus_is_intact():
    """Check the store layout and the canned responses before anything runs.

    Both checks are cheap and both fail in ways that are hard to read later: a
    renamed store file surfaces as a warning line about a content-hash
    mismatch, and a canned response that no longer fits the live schema
    surfaces as a confusing extraction result rather than as an invalid fixture.
    """
    fixture_documents.check_layout()
    fixture_responses.validate_responses()


@pytest.fixture(scope="session")
def documents() -> dict:
    """Both fixture ingests, keyed 'a' and 'b'."""
    return dict(fixture_documents.DOCUMENTS)


@pytest.fixture(scope="session")
def document_a():
    return fixture_documents.DOCUMENT_A


@pytest.fixture(scope="session")
def document_b():
    return fixture_documents.DOCUMENT_B


@pytest.fixture(scope="session")
def ingests_dir() -> Path:
    return fixture_documents.INGESTS_DIR


@pytest.fixture(scope="session")
def expected_extraction() -> dict:
    """The canned model output, keyed (document key, pass name)."""
    return dict(fixture_responses.RESPONSES)


@pytest.fixture
def stubbed_model(monkeypatch) -> list[dict]:
    """Serve the canned responses in place of the model, and log every call.

    Patched on `digester.extract`, not `digester.cli`: `_do_extract` imports
    `extract_two_pass` locally inside the function body, so a patch on the CLI
    module never takes. Returns the call log so a test can assert that exactly
    two passes ran per record.
    """
    from digester import extract

    calls: list[dict] = []

    def _serve(preamble, document, task, model, schema=None, use_api=False):
        calls.append(
            {
                "document": fixture_responses.document_of(document),
                "pass": fixture_responses.pass_of(schema),
                "model": model,
                "use_api": use_api,
                "task": task,
            }
        )
        return fixture_responses.response_for(
            preamble, document, task, model, schema=schema, use_api=use_api
        )

    monkeypatch.setattr(extract, "call_with_document", _serve)
    return calls
