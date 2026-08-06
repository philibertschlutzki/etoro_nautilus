"""Issue #764 (P2) — Fünf bis sechs von sieben Reward-Termen sind inert.

`check_reward_term_variance` (#621/#743) liefert nur eine binäre inert/nicht-inert-Liste als
folgenlose WARNING-Zeile. Fix: `invariants.reward_term_variance_table` liefert je Term `std` UND
`var_contrib` (Anteil an der Summe aller Term-Varianzen) gegen den Zielkorridor `[0.02, 0.30]` —
wird als First-Class-Feld (`reward_term_variance`) in jeden `#742`-Study-Record geschrieben
(Akzeptanzkriterium: "Report enthält die Term-Varianz-Tabelle je Study").

Die tatsächliche Gewichts-Rekalibrierung/Term-Entfernung (Akzeptanzkriterien #1/#2) erfordert eine
REALE Kohorte (>= 50 Studies NACH Kohorte A/B, #753–#763) — die im Issue #764 zitierten
Referenzzahlen stammen aus dem gebrochenen Vor-#753-Suchregime und sind laut der eigenen
Merge-Order-Abhängigkeit des Issues KEINE gültige Evidenz für eine Entscheidung jetzt ("vorher gibt
es keine belastbare eligible Kohorte für die Kalibrierung"). Diese Tests decken daher nur die
Tooling-/Report-Infrastruktur ab (Akzeptanzkriterium #3), nicht fabrizierte Kalibrierwerte.
"""
from automation.optimizer import invariants as inv

_CORRIDOR_LO, _CORRIDOR_HI = inv._REWARD_TERM_VARIANCE_CORRIDOR


def _trial(reward_terms=None, oos_evaluated=True):
    return {"oos_evaluated": oos_evaluated, "reward_terms": reward_terms}


def test_empty_table_when_insufficient_data():
    assert inv.reward_term_variance_table([_trial(None)]) == []
    assert inv.reward_term_variance_table([]) == []


def test_table_covers_all_reward_terms():
    # Issue #927 — gate_distance_penalty/time_box_penalty ergaenzen die urspruenglichen sieben
    # Terme (neun insgesamt); die Tabelle deckt weiterhin JEDEN registrierten Term ab.
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.25 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.2 * i, "gate_distance_penalty": 0.15 * i, "time_box_penalty": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    table = inv.reward_term_variance_table(trials)
    assert {row["term"] for row in table} == set(inv._REWARD_TERM_NUMERIC_KEYS)
    assert len(table) == len(inv._REWARD_TERM_NUMERIC_KEYS)


def test_var_contrib_sums_to_one_across_all_terms():
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.25 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.2 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    table = inv.reward_term_variance_table(trials)
    total = sum(row["var_contrib"] for row in table)
    assert abs(total - 1.0) < 1e-5


def test_constant_term_has_zero_var_contrib_and_is_out_of_corridor():
    """Ein über die gesamte Kohorte KONSTANTER Term (dd_penalty=0.0 überall) hat var_contrib=0.0 —
    strikt unter der 0.02-Untergrenze des Zielkorridors."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.1 * i, "dd_penalty": 0.0,
                "param_pen": 0.02 * i, "turnover": 0.03 * i, "fold_dispersion": 0.01 * i,
                "tie_breaker": 0.001 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    table = inv.reward_term_variance_table(trials)
    dd_row = next(row for row in table if row["term"] == "dd_penalty")
    assert dd_row["var_contrib"] == 0.0
    assert dd_row["in_target_corridor"] is False


def test_dominant_term_is_out_of_corridor_above_the_upper_bound():
    """Ein Term, dessen Streuung die übrigen sechs um Grössenordnungen dominiert, liegt über 0.30."""
    trials = [
        _trial({"branch": "eligible", "base": 100.0 * i, "divergence": 0.01 * i,
                "dd_penalty": 0.01 * i, "param_pen": 0.01 * i, "turnover": 0.01 * i,
                "fold_dispersion": 0.01 * i, "tie_breaker": 0.01 * i})
        for i in range(6)
    ]
    table = inv.reward_term_variance_table(trials)
    base_row = next(row for row in table if row["term"] == "base")
    assert base_row["var_contrib"] > 0.30
    assert base_row["in_target_corridor"] is False


def test_check_reward_term_variance_and_table_agree_on_which_terms_have_no_spread():
    """Konsistenz-Invariante: ein Term, den check_reward_term_variance als inert listet (std < 1%
    von reward_std), hat in der Tabelle immer var_contrib klar unter der 0.02-Korridor-Untergrenze."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.1 * i, "dd_penalty": 0.0,
                "param_pen": 0.02 * i, "turnover": 0.03 * i, "fold_dispersion": 0.01 * i,
                "tie_breaker": 0.001 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    inert = set(inv.check_reward_term_variance(trials).actual)
    table = {row["term"]: row for row in inv.reward_term_variance_table(trials)}
    for term in inert:
        assert table[term]["var_contrib"] < _CORRIDOR_LO


def test_only_non_dominant_varying_terms_land_inside_the_corridor():
    """Alle registrierten Terme etwa GLEICH stark streuend liegen im [0.02, 0.30]-Korridor
    (1/9 ≈ 0.111, Issue #927 — neun statt sieben Terme seit gate_distance_penalty/
    time_box_penalty)."""
    n_terms = len(inv._REWARD_TERM_NUMERIC_KEYS)
    trials = [
        _trial({"branch": "eligible", "base": 0.2 * i, "divergence": 0.2 * i, "dd_penalty": 0.2 * i,
                "param_pen": 0.2 * i, "turnover": 0.2 * i, "fold_dispersion": 0.2 * i,
                "tie_breaker": 0.2 * i, "gate_distance_penalty": 0.2 * i, "time_box_penalty": 0.2 * i})
        for i in range(6)
    ]
    table = inv.reward_term_variance_table(trials)
    for row in table:
        assert row["in_target_corridor"] is True
        assert abs(row["var_contrib"] - 1.0 / n_terms) < 1e-6


# ── report.py wiring: reward_term_variance landet im Study-Record ──────────────────────────────────
def test_study_record_includes_reward_term_variance_table():
    from automation.optimizer import report as rpt

    class _Trial:
        def __init__(self, user_attrs, value=1.0):
            self.user_attrs = user_attrs
            self.value = value

    class _Study:
        def __init__(self, trials):
            self.trials = trials
            self.user_attrs = {}

        @property
        def best_value(self):
            return max(t.value for t in self.trials)

    trials = [
        _Trial({"oos_evaluated": True, "oos_eligible": True,
                "reward_terms": {"branch": "eligible", "base": b, "divergence": 0.3 * i,
                                 "dd_penalty": 0.25 * i, "param_pen": 0.2 * i, "turnover": 0.3 * i,
                                 "fold_dispersion": 0.25 * i, "tie_breaker": 0.2 * i,
                                 "gate_distance_penalty": 0.15 * i, "time_box_penalty": 0.0}})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    study = _Study(trials)
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy", "status": "READY_FOR_PR"}
    record, _checks = rpt._study_record(proposal, study)
    assert "reward_term_variance" in record
    assert len(record["reward_term_variance"]) == len(inv._REWARD_TERM_NUMERIC_KEYS)
    assert {row["term"] for row in record["reward_term_variance"]} == set(inv._REWARD_TERM_NUMERIC_KEYS)
