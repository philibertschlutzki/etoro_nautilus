"""Issue #657 — Kollinearität `min_total_return` ↔ `min_expectancy`.

Beide waren harte Gates, massen aber dieselbe Grösse: `total_return ≈ Σ(expectancy_i)` bzw.
`expectancy ≈ total_return / n_trades`. Bei `min_trades=20` und `min_total_return=0.005` war ein
unabhängiges `min_expectancy=0.001` eine DOPPELTE Kodierung derselben Bedingung.

Fix (nach #650): das absolute `min_expectancy`-Gate blieb zunächst als das EINZIGE absolute
Profitabilitäts-Gate bestehen (kostenrelativ via `oos_min_expectancy_k_alpha`, #562) —
`min_total_return` wurde bereits durch #650 aus `eligible_requires_all` entfernt.

Issue #697 — SUPERSEDIERT diesen Zwischenstand: `min_expectancy` selbst war zu ~96% kollinear mit
`oos_min_psr` (|ρ|=0.961) und wurde ebenfalls aus der Konjunktion entfernt (bleibt als WEICHE
Near-Miss-Distanz über `reward._normalized_gate_distances` erhalten). Es existiert seither KEIN
hartes absolutes Return-/Expectancy-Gate mehr — `oos_min_psr` (risikoadjustiert, skalenfrei) ist
das alleinige harte Netto-Edge-Gate.
"""
import json
from pathlib import Path

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_no_absolute_profitability_gate_remains_after_697():
    """Akzeptanzkriterium (#697, supersediert die #657-Zwischenlage): nach der weiteren
    Konsolidierung existiert KEIN absolutes Return-/Expectancy-Gate mehr in eligible_requires_all —
    das risikoadjustierte oos_min_psr trägt die Netto-Edge-Entscheidung allein."""
    req_all = set(TCFG["eligible_requires_all"])
    absolute_return_gates = req_all & {"min_total_return", "min_expectancy"}
    assert absolute_return_gates == set(), (
        f"Erwartet KEIN absolutes Profitabilitäts-Gate mehr (seit #697), gefunden: "
        f"{absolute_return_gates}"
    )
    assert "oos_min_psr" in req_all


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
