from digester.cli import _producer_from_creators


def test_producer_is_first_real_creator():
    assert _producer_from_creators(["60 Minutes"]) == "60 Minutes"
    assert _producer_from_creators(["Helene Cooper", "Leslie Kean"]) == "Helene Cooper"


def test_producer_skips_annotation_tokens():
    # The 1970 Los Alamos case: a redaction token must not become the producer.
    assert _producer_from_creators(["{{redacted}}", "James L. Tuck"]) == "James L. Tuck"
    assert _producer_from_creators(["{{ redacted }}", "Real Name"]) == "Real Name"


def test_producer_none_when_no_real_creator():
    assert _producer_from_creators([]) is None
    assert _producer_from_creators(None) is None
    assert _producer_from_creators(["{{redacted}}"]) is None
