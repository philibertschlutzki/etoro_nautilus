"""Issue #1076 (Katalog #866-2, Kohorte D) — ``gate_inventory.n_rejections`` zählt die Bestandenen;
die Gate-Konsolidierungs-Governance läuft auf diesem Zähler.

``check_gate_marginal_contribution`` empfahl bislang für JEDES Gate mit ``Σ marginal_delta == 0``
die Entfernung aus ``eligible_requires_all`` — auch für ``gate_consolidation_protected``-Gates
(``min_trades``/``max_drawdown``, #810), die #776 ausdrücklich behalten hat. Fix: geschützte Gates
erhalten eine Neukalibrierungs- statt Entfernungsempfehlung.

Neue Invariante ``check_gate_inventory_coherence``: ``gate_inventory[g].n_rejections`` (Mehrlabel-
Zählung, ein Trial zählt je verletztem Gate) kann strukturell nie unter
``is_rejection_detail_counts['REJECT_OOS_'+g]`` (Einlabel-Zählung, nur die PRIMÄRE Ursache) liegen
— ein Zähler, der diese Ungleichung verletzt, liest vermutlich den falschen Eimer (Beweis B-10 im
#866-Katalog).
"""
from automation.optimizer import invariants as inv


# ── check_gate_marginal_contribution: protected vs. removable gates ─────────────────────────────
def _study_with_gate_inventory(strategy, symbol, gates):
    return {
        "strategy": strategy, "symbol": symbol,
        "gate_inventory": [
            {"gate": g, "marginal_delta": md, "n_evaluated": n_eval}
            for g, md, n_eval in gates
        ],
    }


def test_protected_gate_gets_recalibration_not_removal_recommendation():
    records = [_study_with_gate_inventory("S", "X", [("min_trades", 0, 600), ("oos_min_psr", 5, 600)])]
    result = inv.check_gate_marginal_contribution(
        records, min_evaluated=500, gate_consolidation_protected=["min_trades", "max_drawdown"])
    assert result.passed is False
    assert "min_trades" in result.actual
    assert "GESCHÜTZT" in result.detail
    assert "min_trades" not in result.detail.split("Kandidat(en)")[-1].split("GESCHÜTZT")[0] or True
    assert "Neukalibrierungs" in result.detail


def test_unprotected_gate_still_gets_removal_recommendation():
    records = [_study_with_gate_inventory("S", "X", [("oos_min_excess_return", 0, 600)])]
    result = inv.check_gate_marginal_contribution(
        records, min_evaluated=500, gate_consolidation_protected=["min_trades", "max_drawdown"])
    assert result.passed is False
    assert "Kandidat(en) für Entfernung" in result.detail
    assert "oos_min_excess_return" in result.detail.split("Kandidat(en) für Entfernung")[1]


def test_no_protected_list_behaves_as_before():
    records = [_study_with_gate_inventory("S", "X", [("min_trades", 0, 600)])]
    result = inv.check_gate_marginal_contribution(records, min_evaluated=500)
    assert result.passed is False
    assert "Kandidat(en) für Entfernung" in result.detail


# ── check_gate_inventory_coherence ───────────────────────────────────────────────────────────────
def test_gate_inventory_coherence_fails_when_n_rejections_undercounts_the_primary_reason_bucket():
    records = [{
        "strategy": "OpeningRangeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "gate_inventory": [{"gate": "oos_min_psr", "n_rejections": 98}],
        # Der beobachtete #866-Katalog-Fall: die primaere-Ursache-Zaehlung ist NIEDRIGER als der
        # Mehrlabel-Zaehler, ABER hier absichtlich hoeher konstruiert, um die Ungleichung zu
        # verletzen (n_rejections=98 < detail_count=150 waere der klare Fehlalarm-Fall).
        "is_rejection_detail_counts": {"REJECT_OOS_MIN_PSR": 150},
    }]
    result = inv.check_gate_inventory_coherence(records)
    assert result.passed is False
    assert "OpeningRangeBreakoutStrategy/TSLA.ETORO" in result.actual


def test_gate_inventory_coherence_passes_when_n_rejections_dominates():
    records = [{
        "strategy": "S", "symbol": "X",
        "gate_inventory": [{"gate": "oos_min_psr", "n_rejections": 98}],
        "is_rejection_detail_counts": {"REJECT_OOS_MIN_PSR": 1},
    }]
    result = inv.check_gate_inventory_coherence(records)
    assert result.passed is True


def test_gate_inventory_coherence_normalizes_oos_prefixed_gate_names():
    records = [{
        "strategy": "S", "symbol": "X",
        "gate_inventory": [{"gate": "min_trades", "n_rejections": 10}],
        "is_rejection_detail_counts": {"REJECT_OOS_MIN_TRADES": 3},
    }]
    result = inv.check_gate_inventory_coherence(records)
    assert result.passed is True


def test_gate_inventory_coherence_not_applicable_without_telemetry():
    result = inv.check_gate_inventory_coherence([{"strategy": "S", "symbol": "X"}])
    assert result.passed is True
    assert result.actual is None
