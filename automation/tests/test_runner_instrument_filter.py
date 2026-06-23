"""A4.2 — restrict_universe pure-function tests (near the runner, Tier 3).

intersection + order preservation; unknown symbols dropped without crashing.
"""
from automation.backtest_runner import restrict_universe


def test_restrict_none_is_identity():
    u = ["A.ETORO", "B.ETORO", "C.ETORO"]
    assert restrict_universe(u, None) == u
    assert restrict_universe(u, []) == u


def test_restrict_intersection_preserves_order():
    u = ["A.ETORO", "B.ETORO", "C.ETORO"]
    # filter order (C, A) must NOT change the universe order (A, C)
    assert restrict_universe(u, ["C.ETORO", "A.ETORO"]) == ["A.ETORO", "C.ETORO"]


def test_restrict_unknown_symbol_dropped():
    # unknown symbols are silently dropped (no crash) -> empty result is allowed
    assert restrict_universe(["A.ETORO"], ["Z.ETORO"]) == []


def test_restrict_single_symbol():
    u = ["A.ETORO", "B.ETORO", "C.ETORO"]
    assert restrict_universe(u, ["B.ETORO"]) == ["B.ETORO"]
