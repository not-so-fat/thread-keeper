from threadkeeper import models


def test_context_window_for_known_and_unknown():
    assert models.context_window_for("claude-opus-4-8") == 1_000_000
    assert models.context_window_for("claude-3-5-haiku") == 200_000
    assert models.context_window_for(None) is None
    assert models.context_window_for("some-unknown-model-xyz") is None


def test_pricing_opus_4_8_is_5_25_tier_not_4_1_15_75():
    p48 = models.pricing_for("claude-opus-4-8")
    assert p48 == {"input": 5, "output": 25, "cw5m": 6.25, "cw1h": 10, "cacheRead": 0.5}

    p41 = models.pricing_for("claude-opus-4-1")
    assert p41 == {"input": 15, "output": 75, "cw5m": 18.75, "cw1h": 30, "cacheRead": 1.5}


def test_cost_of_model_known_usage_matches_expected():
    usage = {"input": 1_000_000, "output": 1_000_000, "cacheWrite5m": 1_000_000, "cacheRead": 1_000_000}
    breakdown = models.cost_breakdown_of_model("claude-opus-4-8", usage)
    assert breakdown == {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5}
    assert models.cost_of_model("claude-opus-4-8", usage) == 36.75


def test_cost_of_model_legacy_cache_write_tier_treated_as_5m():
    usage = {"input": 0, "output": 0, "cacheWrite": 1_000_000}
    breakdown = models.cost_breakdown_of_model("claude-sonnet-4-5", usage)
    assert breakdown["cacheWrite"] == 3.75  # sonnet cw5m price


def test_cost_of_model_unpriced_returns_none():
    assert models.cost_of_model("some-unknown-model", {"input": 100}) is None
    assert models.cost_breakdown_of_model(None, {"input": 100}) is None


def test_cost_of_usage_blob_aggregates_across_models():
    usage = {
        "claude-opus-4-8": {"input": 1_000_000, "output": 1_000_000, "cacheRead": 0},
        "claude-haiku": {"input": 1_000_000, "output": 1_000_000, "cacheRead": 0},
    }
    breakdown = models.cost_breakdown_of(usage)
    assert breakdown["input"] == 5.0 + 1.0
    assert breakdown["output"] == 25.0 + 5.0
    assert models.cost_of(usage) == breakdown["input"] + breakdown["output"] + breakdown["cacheWrite"] + breakdown["cacheRead"]


def test_cost_of_empty_or_none_usage_is_zero():
    assert models.cost_of(None) == 0.0
    assert models.cost_of({}) == 0.0


def test_cache_write_tokens_combines_tiers_or_falls_back_to_legacy():
    assert models.cache_write_tokens({"cacheWrite5m": 10, "cacheWrite1h": 5}) == 15
    assert models.cache_write_tokens({"cacheWrite": 7}) == 7
    assert models.cache_write_tokens({}) == 0
