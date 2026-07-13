import pytest

from hippocampus.classify import classify


def test_flags_implementation_detail_as_skip():
    result = classify("renaming the variable name from x to count")
    assert result.weight == "skip"
    assert result.category is None


def test_detects_explicit_deferral():
    result = classify("holding off on multi-region until post-MVP")
    assert result.weight == "deferred"


@pytest.mark.parametrize(
    "description, expected",
    [
        ("use JWT for authentication between services", "security"),
        ("GDPR data residency requires EU-only storage", "compliance"),
        ("chose the cheaper license tier to stay in budget", "cost"),
        ("normalise the orders schema into three tables", "data"),
        ("version the REST endpoint contract via header", "api"),
        ("add a cache layer to cut p99 latency", "performance"),
        ("drop the moment library dependency", "dependency"),
        ("cover the payment flow with e2e tests", "testing"),
        ("retry with backoff, then fall back to the queue", "error-handling"),
        ("keep naming convention consistent across modules", "naming"),
        ("roll back via blue-green deploy", "operational"),
    ],
)
def test_categorises_by_keyword(description, expected):
    assert classify(description).category == expected


def test_security_outranks_data_when_both_match():
    # Security is checked first by design — a decision touching both should
    # land in the more consequential bucket.
    result = classify("encrypt the database schema at rest")
    assert result.category == "security"


@pytest.mark.parametrize("category_hint", ["security", "compliance", "cost"])
def test_sensitive_categories_are_heavy(category_hint):
    mapping = {
        "security": "rotate the credential on every deploy",
        "compliance": "retention policy is 90 days per regulation",
        "cost": "pricing pushed us to build rather than buy",
    }
    assert classify(mapping[category_hint]).weight == "heavy"


def test_ordinary_decision_is_standard():
    assert classify("add a cache layer to cut p99 latency").weight == "standard"


def test_irreversibility_forces_heavy_even_in_light_category():
    result = classify("this test strategy change is irreversible once shipped")
    assert result.weight == "heavy"
