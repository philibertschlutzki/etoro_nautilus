"""Issue #970 (Katalog A, P1) — die Selektion war im Referenzlauf 46cf5070 faktisch eine
Ein-Statistik-Entscheidung: von 2135 evaluierten Trials erschienen in ``oos_rejection_reasons``
genau drei Gates (``oos_min_psr`` 97.3%, ``oos_max_drawdown`` 32.0%, ``oos_min_trades`` 2.6%); die
übrigen sieben konfigurierten ``eligible_requires_all``-Gates lehnten in 2135 Evaluierungen KEIN
einziges Mal ab.

``invariants.gate_inventory_table`` liefert je Gate n_rejections/n_solo_rejections/marginal_delta;
``check_gate_marginal_contribution`` ist der Regressionswächter gegen ein Gate ohne jeden Beitrag.
"""
from automation.optimizer import invariants as inv

_GATES = ("oos_min_psr", "oos_max_drawdown", "oos_min_trades", "oos_min_win_rate")


def _trial(deltas, evaluated=True):
    return {"oos_evaluated": evaluated, "oos_gate_deltas": deltas}


def test_gate_that_never_violates_has_zero_rejections_and_zero_marginal_delta():
    """oos_min_win_rate verletzt nie -- weder n_rejections noch marginal_delta > 0."""
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
        _trial({"oos_min_psr": -0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
    ]
    table = inv.gate_inventory_table(trials, list(_GATES))
    win_rate_row = next(r for r in table if r["gate"] == "oos_min_win_rate")
    assert win_rate_row["n_rejections"] == 0
    assert win_rate_row["n_solo_rejections"] == 0
    assert win_rate_row["marginal_delta"] == 0


def test_dominant_solo_gate_is_correctly_counted():
    """oos_min_psr lehnt solo bei 2 von 3 Trials ab (die anderen Gates sind sauber)."""
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
        _trial({"oos_min_psr": 0.05, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
        _trial({"oos_min_psr": -0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
    ]
    table = inv.gate_inventory_table(trials, list(_GATES))
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert psr_row["n_rejections"] == 2
    assert psr_row["n_solo_rejections"] == 2
    # Ohne oos_min_psr waeren die 2 solo-verworfenen Trials zusaetzlich eligible -> marginal_delta=2.
    assert psr_row["marginal_delta"] == 2


def test_a_gate_that_never_wins_solo_but_co_occurs_has_zero_marginal_delta():
    """oos_max_drawdown verletzt IMMER gemeinsam mit oos_min_psr -- ohne oos_max_drawdown waere der
    Trial trotzdem noch an oos_min_psr gescheitert, marginal_delta bleibt 0."""
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_max_drawdown": 0.05, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
        _trial({"oos_min_psr": -0.1, "oos_max_drawdown": -0.1, "oos_min_trades": -5,
                "oos_min_win_rate": -0.1}),
    ]
    table = inv.gate_inventory_table(trials, list(_GATES))
    dd_row = next(r for r in table if r["gate"] == "oos_max_drawdown")
    assert dd_row["n_rejections"] == 1
    assert dd_row["n_solo_rejections"] == 0
    assert dd_row["marginal_delta"] == 0


def test_ignores_non_evaluated_trials():
    trials = [_trial({"oos_min_psr": 0.1}, evaluated=False)]
    assert inv.gate_inventory_table(trials, list(_GATES)) == []


def test_empty_without_configured_gates():
    assert inv.gate_inventory_table([_trial({"oos_min_psr": 0.1})], []) == []


# ── check_gate_marginal_contribution ──────────────────────────────────────────────────────────────
def test_check_passes_when_all_gates_contribute():
    result = inv.check_gate_marginal_contribution([
        {"gate_inventory": [
            {"gate": "oos_min_psr", "marginal_delta": 12, "n_evaluated": 600},
            {"gate": "oos_min_win_rate", "marginal_delta": 3, "n_evaluated": 600},
        ]},
    ], min_evaluated=500)
    assert result.passed is True


def test_check_fails_for_a_gate_with_zero_contribution_over_a_large_cohort():
    result = inv.check_gate_marginal_contribution([
        {"gate_inventory": [{"gate": "oos_min_win_rate", "marginal_delta": 0, "n_evaluated": 600}]},
    ], min_evaluated=500)
    assert result.passed is False
    assert "oos_min_win_rate" in result.actual


def test_check_passes_for_a_zero_contribution_gate_under_the_cohort_threshold():
    """Zu wenig Beobachtungen (< min_evaluated) fuer eine belastbare Aussage -- kein FAIL."""
    result = inv.check_gate_marginal_contribution([
        {"gate_inventory": [{"gate": "oos_min_win_rate", "marginal_delta": 0, "n_evaluated": 50}]},
    ], min_evaluated=500)
    assert result.passed is True


def test_check_aggregates_across_multiple_studies():
    result = inv.check_gate_marginal_contribution([
        {"gate_inventory": [{"gate": "oos_min_win_rate", "marginal_delta": 0, "n_evaluated": 300}]},
        {"gate_inventory": [{"gate": "oos_min_win_rate", "marginal_delta": 0, "n_evaluated": 300}]},
    ], min_evaluated=500)
    assert result.passed is False


def test_check_noop_without_telemetry():
    result = inv.check_gate_marginal_contribution([{"strategy": "A", "symbol": "X"}])
    assert result.passed is True
