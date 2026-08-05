"""Issue #869 (P2, Katalog #866-#869, GitHub-Issue #760) — vier von sieben Reward-Termen sind
inert; der Reward ist faktisch base-only.

Symptom: 550 ``REWARD_TERM_INERT``-Meldungen über einen Referenzlauf (``divergence``/``tie_breaker``
je 168 von 462 Studies, ``turnover`` 102, ``param_pen`` 82, ``dd_penalty`` 16, ``fold_dispersion``
14). ``tie_breaker`` ist dabei ein FEHLALARM: er ist per Konstruktion (``w_ret · oos_total_return``)
nur bei Gleichständen zwischen sonst identisch bewerteten Kandidaten entscheidungsrelevant —
Inertheit ist sein Normalzustand, keine Eigenschaft eines toten Reward-Terms.

Fix (dieser Katalog-Eintrag, bewusst SCOPE-BEGRENZT — siehe optimizer.json-Schema für die
dokumentierte Deferral-Entscheidung zu Fix Punkt 2/4 des Issue-Texts: eine reale >= 50-Studies-
Kohorte für eine tatsächliche Gewicht-0.0-Deaktivierung existiert in dieser Sandbox nicht):
1. ``optimizer.json['reward_term_variance_exempt']`` (Default ``['tie_breaker']``) — Terme, die von
   der ``REWARD_TERM_INERT``-Warnung (run_optimization.py) UND ``invariants.check_reward_term_
   variance`` ausgenommen sind.
2. ``report._reward_term_variance_aggregate`` — Median/p90 von ``var_contrib`` je Term ÜBER DEN
   VOLLSTÄNDIGEN Lauf (nicht nur je Study), als Report-Evidenz für eine künftige Gewichts-
   Entscheidung (``optimizer.json['reward_term_inert_p90_threshold']``, Default 0.01).
"""
import inspect

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import run_optimization as ro


def _trial(reward_terms=None, oos_evaluated=True):
    return {"oos_evaluated": oos_evaluated, "reward_terms": reward_terms}


def _constant_tie_breaker_trials():
    """tie_breaker ist über die gesamte Kohorte konstant (0.0) — der REALISTISCHE Normalfall
    (keine Gleichstände in dieser Kohorte), NICHT der Ausnahmefall."""
    return [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.25 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]


# ── invariants.check_reward_term_variance: tie_breaker ausgenommen ──────────────────────────────
def test_constant_tie_breaker_is_not_flagged_inert_by_default():
    result = inv.check_reward_term_variance(_constant_tie_breaker_trials())
    assert "tie_breaker" not in result.actual


def test_other_constant_terms_are_still_flagged_inert():
    """Regressionswächter — die Ausnahme gilt NUR für tie_breaker, nicht pauschal für jeden
    konstanten Term (dd_penalty bleibt hier absichtlich 0.0 -> weiterhin ein echter Fehlalarm-Kandidat,
    den ein Betreiber sehen soll)."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.0,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert "dd_penalty" in result.actual
    assert "tie_breaker" not in result.actual


def test_exempt_set_is_configurable():
    """Ein Aufrufer kann die Ausnahmemenge explizit überschreiben (z. B. um sie zu leeren und das
    Pre-#869-Verhalten exakt zu reproduzieren)."""
    result = inv.check_reward_term_variance(_constant_tie_breaker_trials(), exempt=frozenset())
    assert "tie_breaker" in result.actual


def test_passed_becomes_true_once_the_only_inert_term_is_exempted():
    """Ein Trial-Set, dessen EINZIGER inerter Term tie_breaker ist, ist unter der Default-Ausnahme
    vollständig 'passed' (kein Fehlalarm mehr)."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.1 * i, "dd_penalty": 0.05 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is True


# ── invariants.reward_term_variance_table: exempt-Feld ───────────────────────────────────────────
def test_table_marks_tie_breaker_as_exempt():
    table = inv.reward_term_variance_table(_constant_tie_breaker_trials())
    row = next(r for r in table if r["term"] == "tie_breaker")
    assert row["exempt"] is True
    other = next(r for r in table if r["term"] == "dd_penalty")
    assert other["exempt"] is False


# ── run_optimization.py: REWARD_TERM_INERT-Warnung uebersprungen fuer tie_breaker ────────────────
def test_emit_study_summary_source_skips_exempt_terms_from_the_warning():
    """Statt eines vollen Study-/Optuna-Fixtures (teuer, siehe #865-Precedent) verifiziert dieser
    Test direkt am Quelltext, dass die INERT-Warnschleife die konfigurierte Ausnahmemenge
    respektiert -- Regressionswächter gegen ein versehentliches Entfernen der Bedingung."""
    source = inspect.getsource(ro._emit_study_summary)
    assert "_reward_term_inert_exempt" in source
    assert 'k not in _reward_term_inert_exempt' in source


# ── report._reward_term_variance_aggregate: Median/p90 ueber den vollstaendigen Lauf ─────────────
def _study_record_with_table(table_rows):
    return {"reward_term_variance": table_rows}


def test_aggregate_computes_median_and_p90_per_term():
    studies_out = [
        _study_record_with_table([{"term": "divergence", "var_contrib": v, "std": 0.0,
                                   "in_target_corridor": True, "exempt": False}])
        for v in [0.001, 0.002, 0.003, 0.5, 0.6]
    ]
    agg = rpt._reward_term_variance_aggregate(studies_out, {})
    row = agg["terms"]["divergence"]
    assert row["n_studies"] == 5
    assert row["median_var_contrib"] == 0.003
    assert row["p90_var_contrib"] == 0.6


def test_aggregate_flags_deactivation_candidate_below_threshold():
    studies_out = [
        _study_record_with_table([{"term": "divergence", "var_contrib": 0.001, "std": 0.0,
                                   "in_target_corridor": False, "exempt": False}])
        for _ in range(10)
    ]
    agg = rpt._reward_term_variance_aggregate(studies_out, {"reward_term_inert_p90_threshold": 0.01})
    assert agg["terms"]["divergence"]["deactivation_candidate"] is True


def test_aggregate_never_flags_exempt_terms_as_deactivation_candidates():
    """AK — tie_breaker bleibt NIE ein deactivation_candidate, selbst bei durchgehend
    verschwindendem var_contrib (sein Normalzustand)."""
    studies_out = [
        _study_record_with_table([{"term": "tie_breaker", "var_contrib": 0.0, "std": 0.0,
                                   "in_target_corridor": False, "exempt": True}])
        for _ in range(10)
    ]
    agg = rpt._reward_term_variance_aggregate(studies_out, {})
    assert agg["terms"]["tie_breaker"]["exempt"] is True
    assert agg["terms"]["tie_breaker"]["deactivation_candidate"] is False


def test_aggregate_threshold_and_exempt_are_configurable():
    studies_out = [
        _study_record_with_table([{"term": "turnover", "var_contrib": 0.05, "std": 0.0,
                                   "in_target_corridor": False, "exempt": False}])
        for _ in range(10)
    ]
    agg = rpt._reward_term_variance_aggregate(
        studies_out, {"reward_term_inert_p90_threshold": 0.10})
    assert agg["terms"]["turnover"]["deactivation_candidate"] is True
    assert agg["p90_threshold"] == 0.10


def test_aggregate_empty_studies_out_yields_empty_terms():
    agg = rpt._reward_term_variance_aggregate([], {})
    assert agg["terms"] == {}


# ── report._study_record wiring: reward_term_variance ist weiterhin vorhanden (Regression #764) ──
def test_study_record_still_populates_reward_term_variance_field():
    class _T:
        def __init__(self, user_attrs):
            self.user_attrs = user_attrs
            self.value = 1.0

    class _S:
        def __init__(self, trials):
            self.trials = trials
            self.user_attrs = {}

        @property
        def best_value(self):
            return 1.0

    trials = [
        _T({"oos_evaluated": True, "oos_eligible": True,
            "reward_terms": {"branch": "eligible", "base": b, "divergence": 0.3 * i,
                             "dd_penalty": 0.25 * i, "param_pen": 0.2 * i, "turnover": 0.3 * i,
                             "fold_dispersion": 0.25 * i, "tie_breaker": 0.0}})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    study = _S(trials)
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy", "status": "READY_FOR_PR"}
    record, _checks = rpt._study_record(proposal, study)
    assert "reward_term_variance" in record
    tie_row = next(r for r in record["reward_term_variance"] if r["term"] == "tie_breaker")
    assert tie_row["exempt"] is True
