from digester.review_gate import digestibility

RECORD = (
    "---\nschema: anomalica/record/1\ntitle: T\n---\n"
    "# Title\n"
    "<!-- speaker: A -->\n"
    "00:00:01.0 First sentence.\n"
    "00:00:03.0 Second sentence.\n"
    "00:00:05.0 Third sentence.\n"
)
# Content (transcript) lines are at file line numbers 7, 8, 9.


def _sidecar(spans):
    return {"schema": "anomalica/review-coverage/0", "reviews": [{"spans": spans}]}


def test_fully_observed_is_digestible():
    sc = _sidecar([{"from": 7, "to": 9, "kind": "observed"}])
    d = digestibility(RECORD, sc)
    assert d.digestible
    assert d.observed_coverage == 1.0
    assert d.total_units == 3


def test_partially_observed_is_not_digestible():
    sc = _sidecar([{"from": 7, "to": 8, "kind": "observed"}])  # 2 of 3 lines
    d = digestibility(RECORD, sc)
    assert not d.digestible
    assert round(d.observed_coverage, 2) == 0.67
    assert "unobserved" in d.reason


def test_played_does_not_count_as_observed():
    sc = _sidecar(
        [
            {"from": 7, "to": 8, "kind": "observed"},
            {"from": 9, "to": 9, "kind": "played"},
        ]
    )
    d = digestibility(RECORD, sc)
    assert not d.digestible  # observed-only: the played line does not count


def test_no_sidecar_is_not_digestible():
    d = digestibility(RECORD, None)
    assert not d.digestible
    assert d.source == "no-sidecar"


def test_precomputed_workbench_verdict_is_preferred():
    sc = {
        "schema": "anomalica/review-coverage/1",
        "digestible": True,
        "observed_coverage": 1.0,
        "total_units": 3,
    }
    d = digestibility(RECORD, sc)
    assert d.digestible
    assert d.source == "sidecar"


def test_threshold_below_one_allows_partial():
    sc = _sidecar([{"from": 7, "to": 8, "kind": "observed"}])  # 0.67
    assert digestibility(RECORD, sc, threshold=0.6).digestible
    assert not digestibility(RECORD, sc, threshold=0.9).digestible
