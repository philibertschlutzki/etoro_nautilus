"""Issue #657 — Kollinearität `min_total_return` ↔ `min_expectancy`.

Beide waren harte Gates, massen aber dieselbe Grösse: `total_return ≈ Σ(expectancy_i)` bzw.
`expectancy ≈ total_return / n_trades`. Bei `min_trades=20` und `min_total_return=0.005` war ein
unabhängiges `min_expectancy=0.001` eine DOPPELTE Kodierung derselben Bedingung.

Fix (nach #650): das absolute `min_expectancy`-Gate bleibt als das EINZIGE absolute
Profitabilitäts-Gate bestehen (kostenrelativ via `oos_min_expectancy_k_alpha`, #562) —
`min_total_return` wurde bereits durch #650 aus `eligible_requires_all` entfernt. Kein zwei
kollineare harte Return-Gates mehr.
"""
import json
from pathlib import Path

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_exactly_one_absolute_profitability_gate_remains():
    """Akzeptanzkriterium (#657): nach der Konsolidierung existiert GENAU EIN absolutes
    Profitabilitäts-Gate (Breakeven-nach-Kosten, kostenrelativ) in eligible_requires_all."""
    req_all = set(TCFG["eligible_requires_all"])
    absolute_return_gates = req_all & {"min_total_return", "min_expectancy"}
    assert absolute_return_gates == {"min_expectancy"}, (
        f"Erwartet genau EIN absolutes Profitabilitäts-Gate (min_expectancy), gefunden: "
        f"{absolute_return_gates}"
    )


def test_min_total_return_and_min_expectancy_are_not_both_hard_gates():
    """Return- und Expectancy-Gate sind nicht mehr redundant beide hart (#657-Akzeptanzkriterium)."""
    req_all = TCFG["eligible_requires_all"]
    assert not ("min_total_return" in req_all and "min_expectancy" in req_all)


def test_remaining_gate_is_cost_relative_via_k_alpha():
    """Das verbleibende min_expectancy-Gate ist kostenrelativ konfiguriert (k_alpha·c_rt, #562) —
    kein zweites absolutes Return-Gate wurde als Ersatz eingeführt."""
    assert TCFG.get("oos_min_expectancy_k_alpha") is not None


def test_schema_documents_the_consolidation():
    doc = TCFG["_schema"]["fields"]["min_total_return"]
    assert "657" in doc
    assert "genau ein" in doc.lower() or "kollinear" in doc.lower()
