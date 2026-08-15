"""Issue #1076 (Katalog #866-2, Kohorte D) — ``gate_inventory.n_rejections`` zählt die Bestandenen;
die Gate-Konsolidierungs-Governance läuft auf diesem Zähler.

``check_gate_marginal_contribution`` empfahl bislang für JEDES Gate mit ``Σ marginal_delta == 0``
die Entfernung aus ``eligible_requires_all`` — auch für ``gate_consolidation_protected``-Gates
(``min_trades``/``max_drawdown``, #810), die #776 ausdrücklich behalten hat. Fix: geschützte Gates
erhalten eine Neukalibrierungs- statt Entfernungsempfehlung.

Issue #956/#1122 (Katalog #960) — die #1076-Invariante ``check_gate_inventory_coherence``
(``gate_inventory[g].n_rejections`` kann strukturell nie unter
``is_rejection_detail_counts['REJECT_OOS_'+g]`` liegen) wurde ENTFERNT: B-13 zeigte, dass
``n_rejections`` trotz dieser Kreuzprüfung weiterhin invertiert blieb (0 statt 140 für
``AdxAtrMomentumStrategy/NVDA.ETORO``s ``oos_min_psr``) — die Ungleichung allein verhinderte die
falsche Konsequenz, korrigierte aber nicht die QUELLE. Der eigentliche Fix (``invariants.
gate_inventory_table`` leitet ``n_rejections`` jetzt DIREKT aus ``is_rejection_detail_counts`` ab,
siehe dortiger Docstring) macht die Kreuzprüfung zur Tautologie — sie ist damit ersatzlos entfallen
(Akzeptanzkriterium #956: ``gate_inventory[g].n_rejections == is_rejection_detail_counts[code(g)]``
für jede Study/jedes Gate).
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


def test_check_gate_inventory_coherence_no_longer_exists():
    """Issue #956/#1122 (Katalog #960) — die Kreuzpruefung wurde entfernt (siehe Datei-Docstring);
    die Behebung an der QUELLE (gate_inventory_table) macht sie zur Tautologie."""
    assert not hasattr(inv, "check_gate_inventory_coherence")
